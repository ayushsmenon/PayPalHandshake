import sys
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import inspect_channel3_product as script  # noqa: E402


def channel3_response(payload: object, status_code: int = 200) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_successful_product_detail_prints_documented_fields(capsys) -> None:
    payload = {
        "id": "product-123",
        "title": "27-inch 4K monitor",
        "description": "A monitor for testing.",
        "brands": [{"id": "brand-1", "name": "Example Brand"}],
        "images": [{"url": "https://example.com/image.jpg"}],
        "structured_attributes": {"resolution": ["4K"], "gtin": ["0123456789012"]},
        "variants": {"options": [], "selected": [{"name": "Color", "label": "Black"}]},
        "offers": [
            {
                "domain": "example.com",
                "price": {"price": 329, "currency": "USD"},
                "availability": "InStock",
                "condition": "new",
            }
        ],
    }

    with (
        patch.dict("os.environ", {"CHANNEL3_API_KEY": "test-key"}, clear=False),
        patch.object(script.httpx, "get", return_value=channel3_response(payload)) as get,
    ):
        sys.argv = ["inspect_channel3_product.py", "product-123"]
        assert script.main() == 0

    output = capsys.readouterr().out
    assert "Returned Channel3 product ID: product-123" in output
    assert "Brand names: Example Brand" in output
    assert "Product image count: 1" in output
    assert "gtin=0123456789012" in output
    assert "Offer 1: domain=example.com, price=329 USD" in output
    assert "test-key" not in output
    assert get.call_args.args[0].endswith("/product-123")
    assert get.call_args.kwargs["headers"]["x-api-key"] == "test-key"


def test_missing_api_key_stops_before_request(monkeypatch, capsys) -> None:
    monkeypatch.delenv("CHANNEL3_API_KEY", raising=False)

    with (
        patch.object(script, "load_dotenv"),
        patch.object(script.httpx, "get") as get,
    ):
        sys.argv = ["inspect_channel3_product.py", "product-123"]
        assert script.main() == 2
        get.assert_not_called()

    assert "missing or empty" in capsys.readouterr().out


def test_api_error_is_safe(capsys) -> None:
    response = channel3_response({"detail": "secret response detail"}, 404)
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "not found",
        request=httpx.Request("GET", f"{script.PRODUCTS_URL}/product-123"),
        response=httpx.Response(404),
    )

    with (
        patch.dict("os.environ", {"CHANNEL3_API_KEY": "test-key"}, clear=False),
        patch.object(script.httpx, "get", return_value=response),
    ):
        sys.argv = ["inspect_channel3_product.py", "product-123"]
        assert script.main() == 1

    output = capsys.readouterr().out
    assert "product was not found" in output
    assert "secret response detail" not in output
    assert "test-key" not in output


def test_timeout_is_handled(capsys) -> None:
    with (
        patch.dict("os.environ", {"CHANNEL3_API_KEY": "test-key"}, clear=False),
        patch.object(script.httpx, "get", side_effect=httpx.ReadTimeout("timed out")),
    ):
        sys.argv = ["inspect_channel3_product.py", "product-123"]
        assert script.main() == 1

    assert "timed out" in capsys.readouterr().out


def test_unexpected_response_shape_is_handled(capsys) -> None:
    with (
        patch.dict("os.environ", {"CHANNEL3_API_KEY": "test-key"}, clear=False),
        patch.object(
            script.httpx,
            "get",
            return_value=channel3_response({"products": []}),
        ),
    ):
        sys.argv = ["inspect_channel3_product.py", "product-123"]
        assert script.main() == 1

    assert "lacked documented" in capsys.readouterr().out


def test_missing_identifiers_and_optional_fields_are_unavailable(capsys) -> None:
    payload = {
        "id": "product-456",
        "title": "Monitor",
        "description": None,
        "structured_attributes": {"resolution": ["4K"]},
    }

    with (
        patch.dict("os.environ", {"CHANNEL3_API_KEY": "test-key"}, clear=False),
        patch.object(script.httpx, "get", return_value=channel3_response(payload)),
    ):
        sys.argv = ["inspect_channel3_product.py", "product-456"]
        assert script.main() == 0

    output = capsys.readouterr().out
    assert "Explicit product identifiers: unavailable" in output
    assert "Selected variant options: unavailable" in output
    assert "Merchant offer count: unavailable" in output
    assert "Offers: unavailable" in output
