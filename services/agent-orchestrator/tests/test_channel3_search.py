import sys
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import check_channel3_search as script  # noqa: E402


def channel3_response(payload: object, status_code: int = 200) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_successful_search_prints_documented_fields(capsys) -> None:
    payload = {
        "products": [
            {
                "id": "product-123",
                "title": "27-inch 4K monitor",
                "brands": [{"id": "brand-1", "name": "Example Brand"}],
                "offers": [
                    {
                        "domain": "example.com",
                        "price": {"price": 329, "currency": "USD"},
                    }
                ],
            }
        ]
    }

    with (
        patch.dict("os.environ", {"CHANNEL3_API_KEY": "test-key"}, clear=False),
        patch.object(script.httpx, "post", return_value=channel3_response(payload)) as post,
    ):
        assert script.main([]) == 0

    output = capsys.readouterr().out
    assert "27-inch 4K monitor" in output
    assert "Example Brand" in output
    assert "product-123" in output
    assert "329 USD" in output
    assert "GTIN/UPC/product identifier: unavailable" in output
    assert "test-key" not in output
    assert post.call_args.kwargs["headers"]["x-api-key"] == "test-key"
    assert post.call_args.kwargs["json"] == {
        "query": "27-inch 4K monitor",
        "limit": 5,
    }


def test_custom_query_is_sent_correctly() -> None:
    payload = {"products": []}

    with (
        patch.dict("os.environ", {"CHANNEL3_API_KEY": "test-key"}, clear=False),
        patch.object(script.httpx, "post", return_value=channel3_response(payload)) as post,
    ):
        assert script.main(["mechanical keyboard"]) == 0

    assert post.call_args.kwargs["json"] == {
        "query": "mechanical keyboard",
        "limit": 5,
    }


def test_empty_query_is_rejected_before_request(capsys) -> None:
    with patch.object(script.httpx, "post") as post:
        assert script.main(["   "]) == 2
        post.assert_not_called()

    assert "query must not be empty or whitespace-only" in capsys.readouterr().err


def test_missing_api_key_stops_before_request(monkeypatch, capsys) -> None:
    monkeypatch.delenv("CHANNEL3_API_KEY", raising=False)

    with (
        patch.object(script, "load_dotenv"),
        patch.object(script.httpx, "post") as post,
    ):
        assert script.main([]) == 2
        post.assert_not_called()

    assert "missing or empty" in capsys.readouterr().out


def test_api_error_is_safe(capsys) -> None:
    response = channel3_response({"detail": "secret response detail"}, 401)
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "unauthorized",
        request=httpx.Request("POST", script.SEARCH_URL),
        response=httpx.Response(401),
    )

    with (
        patch.dict("os.environ", {"CHANNEL3_API_KEY": "test-key"}, clear=False),
        patch.object(script.httpx, "post", return_value=response),
    ):
        assert script.main([]) == 1

    output = capsys.readouterr().out
    assert "authentication failed" in output
    assert "secret response detail" not in output
    assert "test-key" not in output


def test_timeout_is_handled(capsys) -> None:
    with (
        patch.dict("os.environ", {"CHANNEL3_API_KEY": "test-key"}, clear=False),
        patch.object(
            script.httpx,
            "post",
            side_effect=httpx.ReadTimeout("timed out"),
        ),
    ):
        assert script.main([]) == 1

    assert "timed out" in capsys.readouterr().out


def test_unexpected_response_shape_is_handled(capsys) -> None:
    with (
        patch.dict("os.environ", {"CHANNEL3_API_KEY": "test-key"}, clear=False),
        patch.object(
            script.httpx,
            "post",
            return_value=channel3_response({"items": []}),
        ),
    ):
        assert script.main([]) == 1

    assert "products list" in capsys.readouterr().out
