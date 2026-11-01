"""Strict internal client for Process B's versioned verification endpoint."""

from __future__ import annotations

from typing import Any

import httpx
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class ProcessBCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    verdict: Literal["PASS", "REJECT"]
    detail: str


class ProcessBResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["PASS", "REJECT"]
    payment_ready: bool
    checks: list[ProcessBCheck]


class ProcessBVerificationError(RuntimeError):
    """Raised when Process B cannot provide a valid successful decision."""


class ProcessBClient:
    REQUIRED_CHECKS = {
        "mandate_v2_signature",
        "post_authorization_signature",
        "mandate_v2_expiry",
        "post_authorization_expiry",
        "mandate_binding",
        "order_binding",
        "authorization",
        "authorization_amount",
        "currency",
        "negotiation_id",
        "merchant",
        "spending_cap",
        "authorization_cap",
        "product_references",
    }

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)

    def verify_authorization_binding(
        self,
        payload: dict[str, Any],
    ) -> ProcessBResult:
        try:
            response = self.client.post(
                f"{self.base_url}/api/v2/verify-authorization-binding",
                json=payload,
            )
            response.raise_for_status()
            result = ProcessBResult.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as error:
            raise ProcessBVerificationError(
                "Process B returned no valid verification response."
            ) from error

        if result.verdict != "PASS" or not result.payment_ready:
            raise ProcessBVerificationError(
                "Process B did not approve the payment decision."
            )
        check_names = [check.name for check in result.checks]
        if len(check_names) != len(set(check_names)):
            raise ProcessBVerificationError(
                "Process B returned duplicate verification checks."
            )
        if set(check_names) != self.REQUIRED_CHECKS:
            raise ProcessBVerificationError(
                "Process B returned an unexpected verification-check set."
            )
        if any(check.verdict != "PASS" for check in result.checks):
            raise ProcessBVerificationError(
                "Process B returned a failed verification check."
            )
        return result
