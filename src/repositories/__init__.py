"""Isolated repository and persistence access layer for PayPal Agent Platform."""

from .base import (
    BaseRepository,
    DatabaseSessionManager,
    PersistenceError,
    NotFoundError,
    ReplayViolationError,
    MandateExpiredError,
    MandateAlreadyConsumedError,
)
from .negotiation_repo import NegotiationRepository
from .mandate_repo import MandateRepository
from .consent_repo import ConsentRepository
from .audit_repo import AuditRepository

__all__ = [
    "BaseRepository",
    "DatabaseSessionManager",
    "PersistenceError",
    "NotFoundError",
    "ReplayViolationError",
    "MandateExpiredError",
    "MandateAlreadyConsumedError",
    "NegotiationRepository",
    "MandateRepository",
    "ConsentRepository",
    "AuditRepository",
]
