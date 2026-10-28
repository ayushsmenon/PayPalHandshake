"""Canonical mandate serialization shared by the signing protocol."""

from __future__ import annotations

import json

from .models import SignedMandate


def canonical_mandate_bytes(mandate: SignedMandate) -> bytes:
    """Match mandate_gate.canonical exactly: compact, sorted UTF-8 JSON."""
    return json.dumps(
        mandate.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
