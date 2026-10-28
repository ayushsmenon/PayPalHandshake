import base64
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

PAYMENT_POLICY_ROOT = Path(__file__).resolve().parents[1]
MANDATE_GATE_ROOT = PAYMENT_POLICY_ROOT.parent / "mandate-gate"
sys.path.insert(0, str(PAYMENT_POLICY_ROOT))
sys.path.insert(0, str(MANDATE_GATE_ROOT))

from mandate_gate.crypto import verify_mandate_signature  # noqa: E402
from mandate_gate.gate import evaluate_candidate  # noqa: E402
from mandate_gate.models import SignedMandate as GateMandate  # noqa: E402
from payment_policy.canonical import canonical_mandate_bytes  # noqa: E402
from payment_policy.models import SignedMandate  # noqa: E402
from payment_policy.signing import (  # noqa: E402
    PRIVATE_KEY_ENV,
    PrivateKeyConfigurationError,
    sign_mandate,
)
from scripts.initialize_dev_keys import initialize_key_pair  # noqa: E402


def mandate() -> SignedMandate:
    return SignedMandate(
        schema_version="1",
        negotiation_id="HS-2048",
        paypal_auth_id="AUTH-SYNTHETIC",
        authorized_cap_cents=45000,
        currency="USD",
        authorized_merchant_id="merchant-best-buy-sandbox",
        allowed_product_identifiers=["SYNTH-MONITOR-27-4K"],
        issued_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        mandate_id="mandate-synthetic-process-c",
    )


def test_signing_is_accepted_by_process_b() -> None:
    private_key = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(private_key.private_bytes_raw()).decode("ascii")
    envelope = sign_mandate(mandate(), environment={PRIVATE_KEY_ENV: private_b64})
    public_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode(
        "ascii"
    )
    gate_mandate = GateMandate.model_validate(envelope.mandate.model_dump(mode="json"))

    assert verify_mandate_signature(gate_mandate, envelope.signature_b64, public_b64)


def test_signed_mandate_passes_process_b_checks_but_stays_fail_closed() -> None:
    private_key = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(private_key.private_bytes_raw()).decode("ascii")
    envelope = sign_mandate(mandate(), environment={PRIVATE_KEY_ENV: private_b64})
    public_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode(
        "ascii"
    )
    gate_mandate = GateMandate.model_validate(envelope.mandate.model_dump(mode="json"))
    candidate = {
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
    }

    result = evaluate_candidate(
        gate_mandate,
        envelope.signature_b64,
        candidate,
        public_b64,
        now=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
    )

    assert result.verdict == "REJECT"
    assert result.payment_ready is False
    assert all(
        check.verdict == "PASS"
        for check in result.checks
        if check.name != "trusted_product_references"
    )


def test_modified_mandate_fails_verification() -> None:
    private_key = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(private_key.private_bytes_raw()).decode("ascii")
    envelope = sign_mandate(mandate(), environment={PRIVATE_KEY_ENV: private_b64})
    altered = envelope.mandate.model_copy(update={"authorized_cap_cents": 1})
    public_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode(
        "ascii"
    )

    assert not verify_mandate_signature(
        GateMandate.model_validate(altered.model_dump(mode="json")),
        envelope.signature_b64,
        public_b64,
    )


@pytest.mark.parametrize("value", ["", "not-base64", base64.b64encode(b"short").decode()])
def test_missing_or_malformed_private_key_is_rejected(value: str) -> None:
    with pytest.raises(PrivateKeyConfigurationError):
        sign_mandate(mandate(), environment={PRIVATE_KEY_ENV: value})


def test_private_key_never_appears_in_output(capsys: pytest.CaptureFixture[str]) -> None:
    private_key = Ed25519PrivateKey.generate()
    private_b64 = base64.b64encode(private_key.private_bytes_raw()).decode("ascii")
    sign_mandate(mandate(), environment={PRIVATE_KEY_ENV: private_b64})

    assert private_b64 not in capsys.readouterr().out


def test_initializer_refuses_existing_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private_path = tmp_path / "payment" / ".env"
    public_path = tmp_path / "gate" / ".env"
    private_path.parent.mkdir()
    public_path.parent.mkdir()
    private_path.write_text(f"{PRIVATE_KEY_ENV}=private\n", encoding="utf-8")
    public_path.write_text("MANDATE_GATE_PUBLIC_KEY_B64=public\n", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.initialize_dev_keys._is_git_ignored",
        lambda path: True,
    )

    with pytest.raises(RuntimeError, match="already contain"):
        initialize_key_pair(private_path, public_path)


def test_initializer_stops_for_partial_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private_path = tmp_path / "payment" / ".env"
    public_path = tmp_path / "gate" / ".env"
    private_path.parent.mkdir()
    private_path.write_text(f"{PRIVATE_KEY_ENV}=private\n", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.initialize_dev_keys._is_git_ignored",
        lambda path: True,
    )

    with pytest.raises(RuntimeError, match="Only one side"):
        initialize_key_pair(private_path, public_path)
