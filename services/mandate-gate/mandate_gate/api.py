"""FastAPI adapter for the deterministic mandate gate."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict
from dotenv import load_dotenv

from .crypto import is_valid_public_key_b64
from .gate import evaluate_candidate
from .models import CandidateDeal, SignedMandate, VerificationCheck, VerificationResult

PUBLIC_KEY_ENV = "MANDATE_GATE_PUBLIC_KEY_B64"
SERVICE_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(SERVICE_ENV_PATH, override=False)


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandate: SignedMandate
    signature: str
    candidate: CandidateDeal


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["mandate-gate"]


class ReadyResponse(BaseModel):
    ready: bool
    status: Literal["ready", "not_ready"]
    reason: str | None = None


app = FastAPI(
    title="PAYPAL HANDSHAKE Mandate Gate",
    description="Deterministic signed-mandate verification boundary.",
)


def configured_public_key() -> str | None:
    value = os.getenv(PUBLIC_KEY_ENV, "").strip()
    if not value or not is_valid_public_key_b64(value):
        return None
    return value


def configuration_error_result() -> VerificationResult:
    return VerificationResult(
        verdict="REJECT",
        payment_ready=False,
        checks=[
            VerificationCheck(
                name="public_key_configuration",
                verdict="REJECT",
                detail=(
                    "Mandate-gate public-key configuration is missing or invalid."
                ),
            )
        ],
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="mandate-gate")


@app.get("/ready", response_model=ReadyResponse)
def ready() -> ReadyResponse:
    if configured_public_key() is None:
        return ReadyResponse(
            ready=False,
            status="not_ready",
            reason="MANDATE_GATE_PUBLIC_KEY_B64 is missing or invalid.",
        )
    return ReadyResponse(ready=True, status="ready")


@app.post("/api/v1/verify", response_model=VerificationResult)
def verify(request: VerifyRequest) -> VerificationResult:
    public_key = configured_public_key()
    if public_key is None:
        return configuration_error_result()

    return evaluate_candidate(
        request.mandate,
        request.signature,
        request.candidate,
        public_key,
        trusted_product_references_configured=False,
    )
