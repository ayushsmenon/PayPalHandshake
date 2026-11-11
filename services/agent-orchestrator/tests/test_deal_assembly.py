import sys
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))

from app.channel3 import Channel3Client
from app.main import app


def response(payload: object, status_code: int = 200) -> Mock:
    result = Mock()
    result.status_code = status_code
    result.json.return_value = payload
    result.raise_for_status.return_value = None
    return result


class FakeChannel3Transport:
    def __init__(self, details: dict[str, dict[str, object]]) -> None:
        self.details = details
        self.searches: list[str] = []
        self.product_requests: list[str] = []

    def post(self, url: str, **kwargs: object) -> Mock:
        query = kwargs["json"]["query"]
        self.searches.append(query)
        return response(
            {
                "products": [
                    {"id": product_id}
                    for product_id, detail in self.details.items()
                    if detail["query"] == query
                ]
            }
        )

    def get(self, url: str, **kwargs: object) -> Mock:
        product_id = url.rsplit("/", 1)[-1]
        self.product_requests.append(product_id)
        return response(self.details[product_id]["detail"])


def channel3_details() -> dict[str, dict[str, object]]:
    return {
        "monitor-1": {
            "query": "monitor",
            "detail": {
                "id": "monitor-1",
                "title": "27-inch 4K monitor",
                "offers": [
                    {
                        "domain": "merchant.example",
                        "url": "https://merchant.example/monitor-1",
                        "price": {"price": "329.00", "currency": "USD"},
                        "availability": "InStock",
                    }
                ],
            },
        },
        "keyboard-1": {
            "query": "keyboard",
            "detail": {
                "id": "keyboard-1",
                "title": "Mechanical keyboard",
                "offers": [
                    {
                        "domain": "merchant.example",
                        "url": "https://merchant.example/keyboard-1",
                        "price": {"price": 89.5, "currency": "USD"},
                        "availability": "InStock",
                    }
                ],
            },
        },
    }


def test_assemble_deal_uses_real_search_and_detail_data() -> None:
    transport = FakeChannel3Transport(channel3_details())
    channel3 = Channel3Client(api_key="test-key", transport=transport)

    with patch("app.routes.channel3_client_from_environment", return_value=channel3):
        response_data = TestClient(app).post(
            "/api/v1/deals/assemble",
            json={
                "negotiation_id": "negotiation-1",
                "requirements": ["monitor", "keyboard"],
                "budget_cents": 45000,
                "currency": "USD",
            },
        )

    assert response_data.status_code == 200
    assert response_data.json() == {
        "status": "ASSEMBLED",
        "is_real": True,
        "source": "channel3",
        "negotiation_id": "negotiation-1",
        "budget_cents": 45000,
        "currency": "USD",
        "total_cents": 41850,
        "remaining_budget_cents": 3150,
        "items": [
            {
                "requirement": "monitor",
                "product_identifier": "monitor-1",
                "name": "27-inch 4K monitor",
                "merchant_domain": "merchant.example",
                "offer_url": "https://merchant.example/monitor-1",
                "price_cents": 32900,
                "currency": "USD",
                "availability": "InStock",
                "source": "channel3",
                "illustrative": False,
            },
            {
                "requirement": "keyboard",
                "product_identifier": "keyboard-1",
                "name": "Mechanical keyboard",
                "merchant_domain": "merchant.example",
                "offer_url": "https://merchant.example/keyboard-1",
                "price_cents": 8950,
                "currency": "USD",
                "availability": "InStock",
                "source": "channel3",
                "illustrative": False,
            },
        ],
        "limitations": [],
    }
    assert transport.searches == ["monitor", "keyboard"]
    assert transport.product_requests == ["monitor-1", "keyboard-1"]


def test_assemble_deal_rejects_missing_or_unusable_channel3_facts() -> None:
    details = channel3_details()
    details["monitor-1"]["detail"]["offers"][0]["url"] = None
    transport = FakeChannel3Transport(details)
    channel3 = Channel3Client(api_key="test-key", transport=transport)

    with patch("app.routes.channel3_client_from_environment", return_value=channel3):
        result = TestClient(app).post(
            "/api/v1/deals/assemble",
            json={
                "negotiation_id": "negotiation-2",
                "requirements": ["monitor", "keyboard"],
                "budget_cents": 45000,
                "currency": "USD",
            },
        )

    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "NO_ELIGIBLE_DEAL"
    assert body["is_real"] is False
    assert body["items"] == []
    assert "No usable in-stock Channel3 offer" in body["limitations"][0]


def test_assemble_deal_does_not_select_over_budget_combinations() -> None:
    transport = FakeChannel3Transport(channel3_details())
    channel3 = Channel3Client(api_key="test-key", transport=transport)

    with patch("app.routes.channel3_client_from_environment", return_value=channel3):
        result = TestClient(app).post(
            "/api/v1/deals/assemble",
            json={
                "negotiation_id": "negotiation-3",
                "requirements": ["monitor", "keyboard"],
                "budget_cents": 41849,
                "currency": "USD",
            },
        )

    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "NO_ELIGIBLE_DEAL"
    assert body["total_cents"] == 0
    assert "fits the budget" in body["limitations"][0]


def test_assemble_deal_returns_unavailable_when_channel3_key_is_missing() -> None:
    with patch.dict("os.environ", {}, clear=True):
        result = TestClient(app).post(
            "/api/v1/deals/assemble",
            json={
                "negotiation_id": "negotiation-4",
                "requirements": ["monitor"],
                "budget_cents": 45000,
                "currency": "USD",
            },
        )

    assert result.status_code == 503
    assert result.json()["detail"] == "Channel3 product discovery is currently unavailable."
