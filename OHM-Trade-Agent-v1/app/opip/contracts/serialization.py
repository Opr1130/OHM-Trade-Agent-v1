"""Deterministic serialization helpers for feature-bus evidence.

Canonical JSON and content hashing reuse the proven implementation in
``app.opip.ml.contracts`` rather than growing a second hashing convention that
would silently produce different ids for identical evidence.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping

from app.opip.ml.contracts import canonical_json_bytes, stable_hash
from app.opip.contracts.temporal import require_utc

#: Hash domain for a canonical episode snapshot's *content* hash. Deliberately
#: distinct from the ``SNAP:`` snapshot identity, so a content hash can never be
#: confused with the identity it describes.
EPISODE_SNAPSHOT_HASH_PREFIX = "PSNAP"


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


__all__ = ["canonical_json_bytes", "episode_snapshot_hash", "iso_z", "stable_hash"]


def episode_snapshot_hash(snapshot_payload: Mapping[str, Any]) -> str:
    """Content hash of one canonical episode snapshot payload.

    This is the single implementation of the ``PSNAP:`` content-hash convention.
    It lives at the contracts layer so the canonical event validator and the
    episode-snapshot producer can both bind a snapshot's exact contents without
    either layer depending on the other, and without a second hashing convention
    drifting from this one.

    It is a pure function of the payload: canonical serialization makes it
    independent of key ordering, any content mutation changes the digest, and no
    clock, randomness or process identity takes part. Non-finite numbers are
    rejected rather than silently rendered, so a payload that cannot be hashed
    faithfully is refused instead of hashed anyway.
    """
    if not isinstance(snapshot_payload, Mapping):
        raise ValueError("snapshot payload must be a mapping")
    try:
        encoded = json.dumps(
            dict(snapshot_payload),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("snapshot payload is not canonically serializable") from exc
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    return f"{EPISODE_SNAPSHOT_HASH_PREFIX}:{digest}"
