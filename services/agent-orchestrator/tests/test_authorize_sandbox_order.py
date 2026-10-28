import sys
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import authorize_sandbox_order as script  # noqa: E402


def order_payload(status: str = "APPROVED") -> dict[str, object]:
    return {
        "id": "9EL47348YP786645U",
        "status": status,
        "intent": "AUTHORIZE",
        "purchase_units": [
            {
                "amount": {"currency_code": "USD", "value": "450.00"},
            }
        ],
    }


def response(payload: object, status_code: int = 200) -> Mock:
    result = Mock()
    result.status_code = status_code
    result.json.return_value = payload
    result.raise_for_status.return_value = None
    return result


def test_authorizes_expected_order_with_deterministic_request_id() -> None:
    authorized = order_payload()
    authorized["status"] = "COMPLETED"
    authorized["purchase_units"][0]["payments"] = {
        "authorizations": [
            {
                "id": "AUTH-123",
                "status": "CREATED",
                "amount": {"currency_code": "USD", "value": "450.00"},
            }
        ]
    }

    with (
        patch.object(script, "request_sandbox_access_token", return_value="token"),
        patch.object(script.httpx, "get", return_value=response(order_payload())) as get,
        patch.object(
            script.httpx,
            "post",
            return_value=response(authorized),
        ) as post,
    ):
        sys.argv = ["authorize_sandbox_order.py", "9EL47348YP786645U"]
        assert script.main() == 0

    post_call = post.call_args
    assert post_call.args[0].endswith("/9EL47348YP786645U/authorize")
    assert post_call.kwargs["json"] == {}
    assert post_call.kwargs["headers"]["Prefer"] == "return=representation"
    request_id = post_call.kwargs["headers"]["PayPal-Request-Id"]
    assert request_id == script.authorization_request_id("9EL47348YP786645U")
    assert get.call_count == 1


def test_existing_authorization_skips_post() -> None:
    inspected = order_payload()
    inspected["purchase_units"][0]["payments"] = {
        "authorizations": [{"id": "AUTH-EXISTING", "status": "CREATED"}]
    }

    with (
        patch.object(script, "request_sandbox_access_token", return_value="token"),
        patch.object(script.httpx, "get", return_value=response(inspected)),
        patch.object(script.httpx, "post") as post,
    ):
        sys.argv = ["authorize_sandbox_order.py", "9EL47348YP786645U"]
        assert script.main() == 0
        post.assert_not_called()


def test_unexpected_order_stops_before_authorize() -> None:
    with (
        patch.object(script, "request_sandbox_access_token", return_value="token"),
        patch.object(
            script.httpx,
            "get",
            return_value=response(order_payload(status="CREATED")),
        ),
        patch.object(script.httpx, "post") as post,
    ):
        sys.argv = ["authorize_sandbox_order.py", "9EL47348YP786645U"]
        assert script.main() == 1
        post.assert_not_called()


def test_authorization_timeout_does_not_retry() -> None:
    with (
        patch.object(script, "request_sandbox_access_token", return_value="token"),
        patch.object(script.httpx, "get", return_value=response(order_payload())),
        patch.object(
            script.httpx,
            "post",
            side_effect=httpx.ReadTimeout("timed out"),
        ) as post,
    ):
        sys.argv = ["authorize_sandbox_order.py", "9EL47348YP786645U"]
        assert script.main() == 1
        assert post.call_count == 1
