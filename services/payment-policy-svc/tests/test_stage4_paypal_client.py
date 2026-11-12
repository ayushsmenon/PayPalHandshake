from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from payment_policy.binding_service import issue_post_authorization_binding
from payment_policy.db import PaymentPolicyDbAdapter
from payment_policy.paypal_adapter import ObservedPayPalAuthorization, ObservedPayPalOrder
from payment_policy.paypal_client import (
    PayPalClientError,
    PayPalReconciliationError,
    PayPalSandboxClient,
)
from payment_policy.versioned_models import MandateV2
from src.repositories import DatabaseSessionManager


@pytest.fixture
def db_adapter():
    manager = DatabaseSessionManager("sqlite:///:memory:")
    manager.create_tables()
    adapter = PaymentPolicyDbAdapter(session_manager=manager)
    yield adapter
    manager.drop_tables()


def test_paypal_client_inspect_order_and_auth(db_adapter):
    mock_http = Mock(spec=httpx.Client)

    def mock_post(url: str, **kwargs):
        resp = Mock()
        resp.raise_for_status.return_value = None
        if "/v1/oauth2/token" in url:
            resp.json.return_value = {"access_token": "mock-token-xyz"}
            return resp
        if "/authorize" in url:
            resp.json.return_value = {
                "id": "ORD-123",
                "purchase_units": [
                    {
                        "payments": {
                            "authorizations": [
                                {
                                    "id": "AUTH-789",
                                    "status": "AUTHORIZED",
                                    "amount": {"currency_code": "USD", "value": "450.00"},
                                }
                            ]
                        }
                    }
                ],
            }
            return resp
        if "/v2/checkout/orders" in url:
            resp.json.return_value = {"id": "ORD-123", "status": "CREATED", "links": []}
            return resp
        raise NotImplementedError(url)

    def mock_get(url: str, **kwargs):
        resp = Mock()
        resp.raise_for_status.return_value = None
        if "/v2/checkout/orders/" in url:
            resp.json.return_value = {
                "id": "ORD-123",
                "intent": "AUTHORIZE",
                "status": "APPROVED",
                "purchase_units": [
                    {
                        "amount": {"currency_code": "USD", "value": "450.00"},
                        "payee": {"merchant_id": "MERCH-TEST-PAYPAL"},
                    }
                ],
            }
            return resp
        if "/v2/payments/authorizations/" in url:
            resp.json.return_value = {
                "id": "AUTH-789",
                "status": "AUTHORIZED",
                "amount": {"currency_code": "USD", "value": "450.00"},
                "supplementary_data": {"related_ids": {"order_id": "ORD-123"}},
            }
            return resp
        raise NotImplementedError(url)

    mock_http.post.side_effect = mock_post
    mock_http.get.side_effect = mock_get

    client = PayPalSandboxClient(
        client_id="test_client_id",
        client_secret="test_client_secret",
        db_adapter=db_adapter,
        http_client=mock_http,
    )

    token = client.get_access_token()
    assert token == "mock-token-xyz"

    created = client.create_order(
        authorized_cap_cents=45000,
        currency="USD",
        idempotency_key="REQ-ORDER-001",
    )
    assert created["id"] == "ORD-123"

    observed_order = client.inspect_order("ORD-123")
    assert observed_order.order_id == "ORD-123"
    assert observed_order.intent == "AUTHORIZE"
    assert observed_order.status == "APPROVED"
    assert observed_order.amount_cents == 45000
    assert observed_order.currency == "USD"
    assert observed_order.payee_id == "MERCH-TEST-PAYPAL"

    observed_auth = client.authorize_order("ORD-123", idempotency_key="REQ-AUTH-001")
    assert observed_auth.authorization_id == "AUTH-789"
    assert observed_auth.status == "AUTHORIZED"
    assert observed_auth.amount_cents == 45000

    inspected_auth = client.inspect_authorization("AUTH-789")
    assert inspected_auth.authorization_id == "AUTH-789"
    assert inspected_auth.order_id == "ORD-123"


def test_paypal_client_capture_reconciliation_failure(db_adapter):
    mock_http = Mock(spec=httpx.Client)

    def mock_post(url: str, **kwargs):
        resp = Mock()
        resp.raise_for_status.return_value = None
        if "/v1/oauth2/token" in url:
            resp.json.return_value = {"access_token": "mock-token-xyz"}
            return resp
        if "/capture" in url:
            raise httpx.RequestError("Network timeout during capture", request=Mock())
        raise NotImplementedError(url)

    mock_http.post.side_effect = mock_post

    client = PayPalSandboxClient(
        client_id="test_client_id",
        client_secret="test_client_secret",
        db_adapter=db_adapter,
        http_client=mock_http,
    )

    with pytest.raises(PayPalReconciliationError):
        client.capture_authorization(
            authorization_id="AUTH-FAIL",
            amount_cents=41850,
            currency="USD",
            idempotency_key="REQ-CAPTURE-FAIL-01",
        )


def test_issue_post_authorization_binding_validates_and_signs():
    from datetime import timedelta
    key = Ed25519PrivateKey.generate()
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id="mnd_bind_01",
        negotiation_id=str(uuid4()),
        paypal_order_id="ORD-BIND-01",
        authorized_cap_cents=45000,
        currency="USD",
        allowed_product_categories=["monitors"],
        merchant_restrictions=["bestbuy.com"],
        issued_at=now,
        expires_at=now + timedelta(hours=2),
    )
    order = ObservedPayPalOrder(
        order_id="ORD-BIND-01",
        intent="AUTHORIZE",
        status="APPROVED",
        amount_cents=45000,
        currency="USD",
    )
    auth = ObservedPayPalAuthorization(
        authorization_id="AUTH-BIND-01",
        order_id="ORD-BIND-01",
        status="AUTHORIZED",
        amount_cents=45000,
        currency="USD",
    )

    binding, sig, pk = issue_post_authorization_binding(
        mandate=mandate,
        order=order,
        authorization=auth,
        private_key=key,
    )
    assert binding.schema_version == "post-authorization-binding.v1"
    assert binding.mandate_id == "mnd_bind_01"
    assert binding.paypal_auth_id == "AUTH-BIND-01"
    assert sig != ""
    assert pk != ""

    with pytest.raises(ValueError, match="Order ID mismatch"):
        wrong_order = ObservedPayPalOrder(
            order_id="WRONG-ORDER",
            intent="AUTHORIZE",
            status="APPROVED",
            amount_cents=45000,
            currency="USD",
        )
        issue_post_authorization_binding(mandate, wrong_order, auth, private_key=key)
