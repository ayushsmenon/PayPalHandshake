"""Deterministic signed-mandate verification and candidate-deal gate."""

from .gate import evaluate_candidate
from .models import (
    CandidateDeal,
    ProductLine,
    SignedMandate,
    VerificationResult,
)

__all__ = [
    "CandidateDeal",
    "ProductLine",
    "SignedMandate",
    "VerificationResult",
    "evaluate_candidate",
]
