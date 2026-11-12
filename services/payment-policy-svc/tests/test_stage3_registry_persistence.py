from __future__ import annotations

import base64
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from payment_policy.category_registry import load_product_category_registry, ProductCategoryRegistryError
from payment_policy.db import PaymentPolicyDbAdapter
from payment_policy.product_reference import (
    issue_product_reference_v2,
    canonical_product_reference_v2_bytes,
)
from src.repositories import DatabaseSessionManager


@pytest.fixture
def test_env():
    manager = DatabaseSessionManager("sqlite:///:memory:")
    manager.create_tables()
    adapter = PaymentPolicyDbAdapter(session_manager=manager)
    key = Ed25519PrivateKey.generate()
    env = {
        "CHANNEL3_API_KEY": "test-key-stage3",
        "MANDATE_SIGNING_PRIVATE_KEY_B64": base64.b64encode(
            key.private_bytes_raw()
        ).decode("ascii"),
        "PUBLIC_KEY_B64": base64.b64encode(
            key.public_key().public_bytes_raw()
        ).decode("ascii"),
    }
    yield adapter, env, key
    manager.drop_tables()


def test_populated_category_registry_matches_target_demo_products():
    registry = load_product_category_registry()
    assert registry.version == "channel3-reviewed-2026.11"

    match_monitor = registry.match(
        {"id": "monitor-1", "title": "27-inch 4K monitor"},
        "merchant.example",
    )
    assert match_monitor.canonical_category == "electronics.monitors"

    match_keyboard = registry.match(
        {"id": "keyboard-1", "title": "Mechanical keyboard"},
        "merchant.example",
    )
    assert match_keyboard.canonical_category == "electronics.keyboards"

    with pytest.raises(ProductCategoryRegistryError):
        registry.match(
            {"id": "unregistered-id", "title": "Random item"},
            "merchant.example",
        )


def test_stage3_issuance_and_db_persistence(test_env):
    adapter, env, key = test_env
    registry = load_product_category_registry()

    mock_resp = Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "id": "monitor-1",
        "title": "27-inch 4K monitor",
        "offers": [
            {
                "domain": "merchant.example",
                "url": "https://merchant.example/item-monitor",
                "price": {"price": "329.00", "currency": "USD"},
                "availability": "InStock",
                "condition": "new",
            }
        ],
    }

    neg = adapter.save_negotiation(
        shopper_id="shopper_stage3",
        request_prompt="Search 4K monitor",
        budget_cents=45000,
        currency_code="USD",
    )

    with patch("payment_policy.product_reference.httpx.get", return_value=mock_resp):
        signed_ref = issue_product_reference_v2(
            product_id="monitor-1",
            expected_merchant="merchant.example",
            expected_price="329.00",
            expected_currency="USD",
            registry=registry,
            environment=env,
        )

    assert signed_ref.reference.canonical_category == "electronics.monitors"
    assert signed_ref.reference.price_minor == 32900

    import hashlib
    digest = hashlib.sha256(canonical_product_reference_v2_bytes(signed_ref.reference)).hexdigest()
    public_key_b64 = env["PUBLIC_KEY_B64"]

    saved_pref = adapter.record_product_reference_v2(
        signed_ref=signed_ref,
        negotiation_id=neg.id,
        digest=digest,
        public_key=public_key_b64,
    )

    assert saved_pref.id is not None
    assert saved_pref.canonical_category == "electronics.monitors"
    assert saved_pref.price_cents == 32900
    assert saved_pref.merchant_domain == "merchant.example"
    assert saved_pref.digest == digest
