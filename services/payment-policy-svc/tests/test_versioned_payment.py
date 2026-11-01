import base64
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from payment_policy.api import create_app
from payment_policy.consent import MandateConsentEvidence
from payment_policy.paypal_adapter import (
    MockPayPalAdapter,
    MockAdapterConfigurationError,
    ObservedPayPalAuthorization,
    ObservedPayPalOrder,
)
from payment_policy.process_b_client import ProcessBResult
from payment_policy.process_b_client import ProcessBClient, ProcessBVerificationError
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


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def request_payload() -> tuple[dict[str, object], str]:
    private_key = Ed25519PrivateKey.generate()
    mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id="mandate-v2",
        negotiation_id="negotiation-v2",
        paypal_order_id="ORDER-1",
        authorized_cap_cents=45000,
        currency="USD",
        allowed_product_categories=["electronics"],
        merchant_restrictions=["newegg.com"],
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(days=365),
    )
    binding = PostAuthorizationBindingV1(
        schema_version="post-authorization-binding.v1",
        binding_id="binding-1",
        mandate_id=mandate.mandate_id,
        mandate_digest_b64=mandate_v2_digest_b64(mandate),
        paypal_order_id="ORDER-1",
        paypal_auth_id="AUTH-1",
        authorization_status="AUTHORIZED",
        authorized_amount_cents=45000,
        currency="USD",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(days=365),
    )
    candidate = CandidateDealV2(
        negotiation_id="negotiation-v2",
        paypal_order_id="ORDER-1",
        merchant_id="newegg.com",
        total_cents=41850,
        currency="USD",
        products=[
            ProductLineV2(
                product_identifier="P-1",
                quantity=1,
                description="Monitor",
            )
        ],
    )
    return (
        {
            "mandate": mandate.model_dump(mode="json"),
            "mandate_signature": base64.b64encode(
                private_key.sign(canonical_mandate_v2_bytes(mandate))
            ).decode(),
            "post_authorization_binding": binding.model_dump(mode="json"),
            "binding_signature": base64.b64encode(
                private_key.sign(canonical_post_authorization_binding_v1_bytes(binding))
            ).decode(),
            "candidate": candidate.model_dump(mode="json"),
        },
        base64.b64encode(private_key.public_key().public_bytes_raw()).decode(),
    )


class PassingProcessB:
    def verify_authorization_binding(self, payload: dict[str, object]) -> ProcessBResult:
        return ProcessBResult(
            verdict="PASS",
            payment_ready=True,
            checks=[
                {
                    "name": name,
                    "verdict": "PASS",
                    "detail": "ok",
                }
                for name in ProcessBClient.REQUIRED_CHECKS
            ],
        )


class StaticConsentProvider:
    def __init__(self, evidence: MandateConsentEvidence) -> None:
        self.evidence = evidence

    def get_for_mandate(self, mandate: MandateV2) -> MandateConsentEvidence:
        return self.evidence


def consent_payload(mandate: MandateV2) -> dict[str, object]:
    return {
        "consent_event_id": "consent-1",
        "mandate_id": mandate.mandate_id,
        "mandate_digest_b64": mandate_v2_digest_b64(mandate),
        "negotiation_id": mandate.negotiation_id,
        "shopper_account_id": "shopper-1",
        "approved_cap_cents": mandate.authorized_cap_cents,
        "currency": mandate.currency,
        "approved_constraints": mandate.allowed_product_categories,
        "approved_merchant_restrictions": mandate.merchant_restrictions,
        "consented_at": (NOW + timedelta(days=30)).isoformat(),
        "expires_at": (NOW + timedelta(days=365)).isoformat(),
    }


def test_dry_run_is_read_only_and_requires_independent_paypal_observation(
) -> None:
    payload_data, _ = request_payload()
    mandate = MandateV2.model_validate(payload_data["mandate"])
    evidence = MandateConsentEvidence.model_validate(consent_payload(mandate))
    application = create_app(
        paypal_adapter=MockPayPalAdapter(
            ObservedPayPalOrder("ORDER-1", "AUTHORIZE", "APPROVED", 45000, "USD"),
            ObservedPayPalAuthorization("AUTH-1", "ORDER-1", "AUTHORIZED", 45000, "USD"),
            environment="test",
        ),
        process_b_client=PassingProcessB(),
        consent_provider=StaticConsentProvider(evidence),
        environment="test",
    )
    response = TestClient(application).post("/internal/v1/payment-decisions/dry-run", json=payload_data)

    assert response.status_code == 200
    assert response.json()["decision"] == "APPROVE"
    assert response.json()["payment_ready"] is True
    assert response.json()["mandate_consent_verified"] is True


def test_dry_run_rejects_paypal_observation_mismatch() -> None:
    payload, _ = request_payload()
    application = create_app(
        paypal_adapter=MockPayPalAdapter(
            ObservedPayPalOrder("ORDER-1", "CAPTURE", "APPROVED", 45000, "USD"),
            ObservedPayPalAuthorization("AUTH-1", "ORDER-1", "AUTHORIZED", 45000, "USD"),
            environment="test",
        ),
        process_b_client=PassingProcessB(),
        environment="test",
    )

    response = TestClient(application).post(
        "/internal/v1/payment-decisions/dry-run", json=payload
    )

    assert response.json()["decision"] == "REJECT"
    assert response.json()["payment_ready"] is False


def test_dry_run_requires_separate_mandate_consent() -> None:
    payload, _ = request_payload()
    application = create_app(
        paypal_adapter=MockPayPalAdapter(
            ObservedPayPalOrder("ORDER-1", "AUTHORIZE", "APPROVED", 45000, "USD"),
            ObservedPayPalAuthorization("AUTH-1", "ORDER-1", "AUTHORIZED", 45000, "USD"),
            environment="test",
        ),
        process_b_client=PassingProcessB(),
        environment="test",
    )
    response = TestClient(application).post(
        "/internal/v1/payment-decisions/dry-run", json=payload
    )

    assert response.json()["security_checks_passed"] is True
    assert response.json()["mandate_consent_verified"] is False
    assert response.json()["authorization_permitted"] is False


def test_dry_run_rejects_missing_or_mismatched_payee_identity() -> None:
    payload, _ = request_payload()
    mandate = MandateV2.model_validate(payload["mandate"]).model_copy(
        update={"expected_paypal_payee_id": "PAYEE-1"}
    )
    payload["mandate"] = mandate.model_dump(mode="json")
    evidence = MandateConsentEvidence.model_validate(consent_payload(mandate))
    for payee_id in (None, "PAYEE-OTHER"):
        application = create_app(
            paypal_adapter=MockPayPalAdapter(
                ObservedPayPalOrder(
                    "ORDER-1", "AUTHORIZE", "APPROVED", 45000, "USD", payee_id
                ),
                ObservedPayPalAuthorization(
                    "AUTH-1", "ORDER-1", "AUTHORIZED", 45000, "USD"
                ),
                environment="test",
            ),
            process_b_client=PassingProcessB(),
                consent_provider=StaticConsentProvider(evidence),
            environment="test",
        )
        response = TestClient(application).post(
            "/internal/v1/payment-decisions/dry-run", json=payload
        )
        assert response.json()["authorization_permitted"] is False


def test_dry_run_accepts_matching_payee_identity() -> None:
    payload, _ = request_payload()
    mandate = MandateV2.model_validate(payload["mandate"]).model_copy(
        update={"expected_paypal_payee_id": "PAYEE-1"}
    )
    payload["mandate"] = mandate.model_dump(mode="json")
    evidence = MandateConsentEvidence.model_validate(consent_payload(mandate))
    application = create_app(
        paypal_adapter=MockPayPalAdapter(
            ObservedPayPalOrder(
                "ORDER-1", "AUTHORIZE", "APPROVED", 45000, "USD", "PAYEE-1"
            ),
            ObservedPayPalAuthorization(
                "AUTH-1", "ORDER-1", "AUTHORIZED", 45000, "USD"
            ),
            environment="test",
        ),
        process_b_client=PassingProcessB(),
        consent_provider=StaticConsentProvider(evidence),
        environment="test",
    )
    response = TestClient(application).post(
        "/internal/v1/payment-decisions/dry-run", json=payload
    )
    assert response.json()["authorization_permitted"] is True


def test_mock_adapter_is_rejected_in_production() -> None:
    with pytest.raises(MockAdapterConfigurationError):
        MockPayPalAdapter(
            ObservedPayPalOrder("ORDER-1", "AUTHORIZE", "APPROVED", 45000, "USD"),
            ObservedPayPalAuthorization(
                "AUTH-1", "ORDER-1", "AUTHORIZED", 45000, "USD"
            ),
            environment="production",
        )


def test_process_b_client_rejects_failed_or_malformed_responses() -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"verdict": "REJECT", "payment_ready": False, "checks": []}

    class Client:
        def post(self, url: str, json: dict[str, object]) -> Response:
            return Response()

    with pytest.raises(ProcessBVerificationError):
        ProcessBClient("http://process-b", client=Client()).verify_authorization_binding({})


@pytest.mark.parametrize(
    "verdict,payment_ready",
    [("REJECT", True), ("PASS", False)],
)
def test_process_b_client_rejects_inconsistent_overall_result(
    verdict: str, payment_ready: bool
) -> None:
    checks = [
        {"name": name, "verdict": "PASS", "detail": "ok"}
        for name in ProcessBClient.REQUIRED_CHECKS
    ]

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "verdict": verdict,
                "payment_ready": payment_ready,
                "checks": checks,
            }

    class Client:
        def post(self, url: str, json: dict[str, object]) -> Response:
            return Response()

    with pytest.raises(ProcessBVerificationError):
        ProcessBClient("http://process-b", client=Client()).verify_authorization_binding({})


def test_process_b_client_rejects_missing_duplicate_and_unknown_checks() -> None:
    required = [
        {"name": name, "verdict": "PASS", "detail": "ok"}
        for name in ProcessBClient.REQUIRED_CHECKS
    ]

    class Response:
        def raise_for_status(self) -> None:
            return None

        def __init__(self, checks):
            self.checks = checks

        def json(self) -> dict[str, object]:
            return {"verdict": "PASS", "payment_ready": True, "checks": self.checks}

    class Client:
        def __init__(self, checks):
            self.checks = checks

        def post(self, url: str, json: dict[str, object]) -> Response:
            return Response(self.checks)

    for checks in (
        required[:-1],
        required + [required[0]],
        required + [{"name": "unknown", "verdict": "PASS", "detail": "ok"}],
    ):
        with pytest.raises(ProcessBVerificationError):
            ProcessBClient(
                "http://process-b", client=Client(checks)
            ).verify_authorization_binding({})


def test_process_b_client_rejects_non_pass_and_malformed_checks() -> None:
    checks = [
        {"name": name, "verdict": "PASS", "detail": "ok"}
        for name in ProcessBClient.REQUIRED_CHECKS
    ]
    checks[0]["verdict"] = "REJECT"

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"verdict": "PASS", "payment_ready": True, "checks": checks}

    class Client:
        def post(self, url: str, json: dict[str, object]) -> Response:
            return Response()

    with pytest.raises(ProcessBVerificationError):
        ProcessBClient("http://process-b", client=Client()).verify_authorization_binding({})

    checks[0] = {"name": "mandate_v2_signature", "verdict": "PASS"}
    with pytest.raises(ProcessBVerificationError):
        ProcessBClient("http://process-b", client=Client()).verify_authorization_binding({})
