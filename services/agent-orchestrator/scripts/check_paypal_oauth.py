"""Safely verify PayPal Sandbox client-credentials OAuth locally."""

from __future__ import annotations

import sys

import httpx

from paypal_auth import load_sandbox_credentials


def main() -> int:
    try:
        client_id, client_secret = load_sandbox_credentials()
    except ValueError as error:
        print(f"OAuth check stopped: {error}")
        return 2

    try:
        response = httpx.post(
            "https://api-m.sandbox.paypal.com/v1/oauth2/token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        print(
            "OAuth check failed: PayPal returned "
            f"HTTP {error.response.status_code}."
        )
        return 1
    except httpx.RequestError as error:
        print(f"OAuth check failed: network error ({error.__class__.__name__}).")
        return 1

    try:
        payload = response.json()
    except ValueError:
        print("OAuth check failed: PayPal returned invalid JSON.")
        return 1

    expires_in = payload.get("expires_in")
    if not isinstance(expires_in, int):
        print("OAuth check failed: response did not include a valid token expiry.")
        return 1

    print("PayPal Sandbox OAuth smoke test succeeded.")
    print(f"HTTP status: {response.status_code}")
    print(f"Token expiry: {expires_in} seconds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
