import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandate_gate.canonical import canonical_mandate_bytes
from mandate_gate.gate import evaluate_candidate
from mandate_gate.models import CandidateDeal, SignedMandate


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def signed_context() -> tuple[dict[str, object], str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    mandate = SignedMandate(
        schema_version="1",
        negotiation_id="HS-2048",
        paypal_auth_id="AUTH-SYNTHETIC",
        authorized_cap_cents=45000,
        currency="USD",
        authorized_merchant_id="merchant-best-buy-sandbox",
        allowed_product_identifiers=["SYNTH-MONITOR-27-4K", "SYNTH-KEYBOARD-MECH"],
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(hours=1),
        mandate_id="mandate-synthetic-1",
    )
    signature = private_key.sign(canonical_mandate_bytes(mandate))
    return (
        mandate.model_dump(mode="json"),
        base64.b64encode(signature).decode("ascii"),
        base64.b64encode(public_key.public_bytes_raw()).decode("ascii"),
    )


def candidate() -> dict[str, object]:
    return {
        "negotiation_id": "HS-2048",
        "paypal_auth_id": "AUTH-SYNTHETIC",
        "merchant_id": "merchant-best-buy-sandbox",
        "total_cents": 41850,
        "currency": "USD",
        "products": [
            {
                "product_identifier": "SYNTH-MONITOR-27-4K",
                "quantity": 1,
                "description": "Synthetic 27-inch 4K monitor",
            },
            {
                "product_identifier": "SYNTH-KEYBOARD-MECH",
                "quantity": 1,
                "description": "Synthetic mechanical keyboard",
            },
        ],
    }


def run(context: tuple[dict[str, object], str, str], deal: object = None, **kwargs: object):
    mandate, signature, public_key = context
    return evaluate_candidate(
        mandate,
        signature,
        candidate() if deal is None else deal,
        public_key,
        now=NOW,
        **kwargs,
    )


def test_valid_signature_still_rejects_without_trusted_references(signed_context) -> None:
    result = run(signed_context)

    assert result.verdict == "REJECT"
    assert result.payment_ready is False
    assert {check.name for check in result.checks if check.verdict == "REJECT"} == {
        "trusted_product_references"
    }


def test_tampered_mandate_is_rejected(signed_context) -> None:
    mandate, signature, public_key = signed_context
    mandate["authorized_cap_cents"] = 99999

    result = evaluate_candidate(mandate, signature, candidate(), public_key, now=NOW)

    assert result.verdict == "REJECT"
    assert any(check.name == "mandate_signature" and check.verdict == "REJECT" for check in result.checks)


def test_invalid_signature_is_rejected(signed_context) -> None:
    mandate, _, public_key = signed_context

    result = evaluate_candidate(mandate, base64.b64encode(b"bad").decode(), candidate(), public_key, now=NOW)

    assert result.verdict == "REJECT"


def test_expired_mandate_is_rejected(signed_context) -> None:
    mandate_data, _, _ = signed_context
    mandate_data["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
    expired_mandate = SignedMandate.model_validate(mandate_data)
    private_key = Ed25519PrivateKey.generate()
    signature = base64.b64encode(
        private_key.sign(canonical_mandate_bytes(expired_mandate))
    ).decode("ascii")
    public_key = base64.b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode("ascii")

    result = evaluate_candidate(
        expired_mandate.model_dump(mode="json"),
        signature,
        candidate(),
        public_key,
        now=NOW,
    )

    assert result.verdict == "REJECT"
    assert any(check.name == "mandate_expiry" and check.verdict == "REJECT" for check in result.checks)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("total_cents", 45001),
        ("paypal_auth_id", "AUTH-OTHER"),
        ("merchant_id", "merchant-other"),
    ],
)
def test_candidate_mismatch_is_rejected(signed_context, field: str, value: object) -> None:
    deal = candidate()
    deal[field] = value
    result = run(signed_context, deal)

    assert result.verdict == "REJECT"


def test_missing_and_mismatched_product_identifiers_are_rejected(signed_context) -> None:
    missing = candidate()
    missing["products"] = [missing["products"][0]]
    mismatched = candidate()
    mismatched["products"][0]["product_identifier"] = "SYNTH-OTHER"

    assert run(signed_context, missing).verdict == "REJECT"
    assert run(signed_context, mismatched).verdict == "REJECT"


def test_malformed_candidate_is_rejected(signed_context) -> None:
    malformed = candidate()
    malformed["products"][0]["quantity"] = 0

    result = run(signed_context, malformed)

    assert result.verdict == "REJECT"
    assert result.checks[-1].name == "candidate_shape"


def test_trusted_reference_configuration_allows_pass(signed_context) -> None:
    result = run(signed_context, trusted_product_references_configured=True)

    assert result.verdict == "PASS"
    assert result.payment_ready is True
