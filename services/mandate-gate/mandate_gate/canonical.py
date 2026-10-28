"""Canonical serialization for signed mandates."""

from __future__ import annotations

import json

from .models import SignedMandate


def canonical_mandate_bytes(mandate: SignedMandate) -> bytes:
    """Return the documented UTF-8, sorted-key, compact JSON representation."""
    payload = mandate.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
