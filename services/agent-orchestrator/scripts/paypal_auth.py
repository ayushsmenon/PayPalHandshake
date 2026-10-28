"""Shared local PayPal Sandbox authentication helpers."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

TOKEN_URL = "https://api-m.sandbox.paypal.com/v1/oauth2/token"
SERVICE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = SERVICE_DIR / ".env"


def load_sandbox_credentials() -> tuple[str, str]:
    load_dotenv(ENV_PATH, override=False)

    environment = os.getenv("PAYPAL_ENV", "sandbox").strip().lower()
    client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()

    if environment != "sandbox":
        raise ValueError("PAYPAL_ENV must be set to sandbox.")
    if not client_id or not client_secret:
        raise ValueError(
            "Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in "
            "services/agent-orchestrator/.env."
        )

    return client_id, client_secret


def request_sandbox_access_token() -> str:
    client_id, client_secret = load_sandbox_credentials()
    response = httpx.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=httpx.Timeout(15.0, connect=5.0),
    )
    response.raise_for_status()

    payload = response.json()
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("PayPal did not return an access token.")
    return token
