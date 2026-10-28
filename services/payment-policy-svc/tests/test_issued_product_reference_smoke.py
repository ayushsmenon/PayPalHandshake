import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT / "scripts"))

from smoke_test_issued_product_reference import (  # noqa: E402
    build_request,
    load_reference,
)
from payment_policy.product_reference import (  # noqa: E402
    ProductReference,
    sign_product_reference,
)


def test_load_and_embed_exact_issued_reference(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    issued_at = datetime(2026, 10, 28, tzinfo=timezone.utc)
    reference = sign_product_reference(
        ProductReference(
            schema_version="product-reference.v1",
            channel3_product_id="PpawoVb",
            offer_url="https://newegg.com/p/product",
            merchant_domain="newegg.com",
            price_minor=14399,
            currency="USD",
            availability="InStock",
            condition="new",
            issued_at=issued_at,
            expires_at=issued_at + timedelta(hours=1),
        ),
        private_key,
    )
    path = tmp_path / "issued.product-reference.json"
    path.write_text(json.dumps(reference.model_dump(mode="json")), encoding="utf-8")

    loaded = load_reference(path)
    request = build_request(
        loaded,
        environment={
            "MANDATE_SIGNING_PRIVATE_KEY_B64": base64.b64encode(
                private_key.private_bytes_raw()
            ).decode("ascii")
        },
        now=issued_at,
    )

    candidate_product = request["candidate"]["products"][0]
    assert candidate_product["product_reference"] == reference.model_dump(mode="json")
    assert candidate_product["product_identifier"] == reference.reference.channel3_product_id
    assert candidate_product["offer_url"] == reference.reference.offer_url
    assert candidate_product["unit_price_cents"] == reference.reference.price_minor
    assert request["candidate"]["merchant_id"] == reference.reference.merchant_domain
