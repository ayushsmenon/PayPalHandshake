from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_verify_and_capture_flow_end_to_end() -> None:
    verify_resp = client.post(
        "/api/v1/deals/verify",
        json={"negotiation_id": "HS-2048"},
    )
    assert verify_resp.status_code == 200
    v_data = verify_resp.json()
    assert v_data["verdict"] == "PASS"
    assert v_data["payment_ready"] is True
    assert len(v_data["checks"]) == 14
    assert len(v_data["products"]) == 2
    assert v_data["mandate_id"] == "mnd_hs-2048"

    capture_resp = client.post(
        "/api/v1/deals/approve-and-capture",
        json={
            "negotiation_id": "HS-2048",
            "mandate_id": v_data["mandate_id"],
            "shopper_id": "shopper_avery_singh",
            "confirmed_amount_cents": 41850,
            "confirmed_merchant": "merchant.example",
        },
    )
    assert capture_resp.status_code == 200
    c_data = capture_resp.json()
    assert c_data["status"] == "COMPLETED"
    assert c_data["captured_amount_cents"] == 41850
    assert c_data["paypal_order_id"] == "ORD-HS2048-SANDBOX"
    assert c_data["paypal_capture_id"].startswith("CAPTURE-HS2048-")

    audit_resp = client.get("/api/v1/audit/trail/HS-2048")
    assert audit_resp.status_code == 200
    a_data = audit_resp.json()
    assert len(a_data["events"]) >= 2
    event_types = {e["event_type"] for e in a_data["events"]}
    assert "VERIFICATION" in event_types
    assert "PAYMENT" in event_types
