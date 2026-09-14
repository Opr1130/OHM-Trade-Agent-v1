"""Deterministic serialization helpers for feature-bus evidence.

Canonical JSON and content hashing reuse the proven implementation in
``app.opip.ml.contracts`` rather than growing a second hashing convention that
would silently produce different ids for identical evidence.
"""

from __future__ import annotations

from datetime import datetime

from app.opip.ml.contracts import canonical_json_bytes, stable_hash
from app.opip.contracts.temporal import require_utc


def iso_z(value: datetime, *, field_name: str = "timestamp") -> str:
    """Render a UTC timestamp in the ``Z`` form used by the v1.2 fixtures.

    Whole seconds render without a fractional part, sub-second precision
    renders as milliseconds when exact and microseconds otherwise. The output
    is stable, so it is safe inside ids and idempotency keys.
    """
    moment = require_utc(value, field_name=field_name)
    if moment.microsecond == 0:
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    if moment.microsecond % 1000 == 0:
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}Z"


__all__ = ["canonical_json_bytes", "iso_z", "stable_hash"]
