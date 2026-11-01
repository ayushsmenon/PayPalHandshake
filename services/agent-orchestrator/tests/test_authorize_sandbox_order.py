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


def test_main_never_authorizes_or_contacts_paypal() -> None:
    with patch.object(script.httpx, "post") as post, patch.object(script.httpx, "get") as get:
        sys.argv = ["authorize_sandbox_order.py", "9EL47348YP786645U"]
        assert script.main() == 2
        post.assert_not_called()
        get.assert_not_called()


def test_validation_helpers_remain_meaningful() -> None:
    inspected = order_payload()
    inspected["purchase_units"][0]["payments"] = {
        "authorizations": [{"id": "AUTH-EXISTING", "status": "CREATED"}]
    }

    assert script.validate_order(inspected, "9EL47348YP786645U") == (True, "")
    assert script.existing_authorizations(inspected)[0]["id"] == "AUTH-EXISTING"


def test_unexpected_order_is_rejected_by_validation_helper() -> None:
    assert script.validate_order(
        order_payload(status="CREATED"), "9EL47348YP786645U"
    )[0] is False


def test_imported_main_is_also_disabled() -> None:
    with patch.object(script.httpx, "post") as post, patch.object(script.httpx, "get") as get:
        assert script.main() == 2
        post.assert_not_called()
        get.assert_not_called()
