from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "agent-orchestrator",
    }


def test_demo_negotiation_returns_expected_values() -> None:
    response = client.get("/api/v1/negotiations/demo")
    data = response.json()

    assert response.status_code == 200
    assert data["is_demo"] is True
    assert data["negotiation_id"] == "HS-2048"
    assert data["shopping_goal"] == "27-inch 4K monitor + mechanical keyboard"
    assert data["merchant"] == "Best Buy Marketplace"
    assert data["currency"] == "USD"
    assert data["market_reference_cents"] == 47800
    assert data["authorized_budget_cents"] == 45000
    assert data["proposed_total_cents"] == 41850
    assert data["captured_amount_cents"] == 0
    assert data["status"] == "VERIFYING"


def test_demo_negotiation_uses_integer_cents() -> None:
    data = client.get("/api/v1/negotiations/demo").json()

    money_fields = [
        "market_reference_cents",
        "authorized_budget_cents",
        "proposed_total_cents",
        "captured_amount_cents",
    ]
    assert all(isinstance(data[field], int) for field in money_fields)
    assert [product["price_cents"] for product in data["products"]] == [32900, 8950]
    assert [event["amount_cents"] for event in data["history"]] == [
        47800,
        46500,
        41000,
        41850,
    ]


def test_verification_states_and_capture_are_truthful() -> None:
    data = client.get("/api/v1/negotiations/demo").json()

    assert [check["state"] for check in data["verification_checks"]] == [
        "PENDING",
        "RUNNING",
        "PENDING",
        "PRELIMINARY",
        "PENDING",
    ]
    assert data["captured_amount_cents"] == 0
    assert all(product["illustrative"] for product in data["products"])
    assert all(product["verified"] is False for product in data["products"])
    assert all(product["product_identifier"] is None for product in data["products"])


def test_cors_is_limited_to_local_frontend() -> None:
    allowed = client.get(
        "/health",
        headers={"Origin": "http://localhost:3000"},
    )
    denied = client.get(
        "/health",
        headers={"Origin": "https://example.com"},
    )

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-origin" not in denied.headers
