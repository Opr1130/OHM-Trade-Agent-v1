"""Canonical episode snapshot v1 shape and identity contract.

The canonical episode snapshot is the production record of what O'Pip observed
for one pair at one scan decision boundary. It is built by the episode-capture
producer and consumed as lineage by Paper v2, so two layers must agree on its
shape: the producer that emits it and the canonical validator that accepts it as
execution evidence.

This module is that single, neutral definition. It lives at the contracts layer
because both layers may depend on contracts, while contracts must never depend on
a producer service. It owns exactly three things and no behavior beyond them:

* the exact v1 field set;
* the deterministic ``EP:``/``SNAP:`` identity derivation;
* a strict validator, so an under-shaped mapping that merely *claims* to be an
  episode snapshot cannot be accepted as one.

Preserving existing identities
------------------------------

The identity derivation below reproduces the producer's existing algorithm
byte-for-byte: the digest is a truncated SHA-256 of the plain, pipe-delimited
identity string, not of a canonical-JSON object. It is deliberately not
``serialization.stable_hash``, which hashes canonical JSON and would produce
different ids and invalidate every historical episode and snapshot identity.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

CANONICAL_EPISODE_SNAPSHOT_RECORD_TYPE = "CANONICAL_EPISODE_SNAPSHOT"

#: The only supported canonical episode snapshot schema version.
CANONICAL_EPISODE_SNAPSHOT_SCHEMA_VERSION = 1

EPISODE_ID_PREFIX = "EP"
SNAPSHOT_ID_PREFIX = "SNAP"
_EPISODE_DIGEST_LENGTH = 24
_SNAPSHOT_DIGEST_LENGTH = 32

#: The exact v1 field set the episode-capture producer emits. A payload with a
#: missing or extra field is not a canonical episode snapshot, so an under-shaped
#: mapping cannot pass itself off as one.
CANONICAL_EPISODE_SNAPSHOT_FIELDS = frozenset(
    {
        "record_type",
        "schema_version",
        "snapshot_id",
        "episode_id",
        "cohort_id",
        "cohort_position",
        "cohort_size",
        "decision_at_utc",
        "symbol",
        "base_asset",
        "kraken_public_symbol",
        "reference_price",
        "last_price",
        "volume_24h",
        "liquidity_24h_usd_approx",
        "high_24h",
        "low_24h",
        "lift_from_24h_low_pct",
        "distance_from_24h_high_pct",
        "ml_feature_seed",
        "signal_quality_enabled",
        "decision_status",
        "candidate_rank",
        "signal_quality_universe_size",
        "stage",
        "pattern",
        "opportunity_score",
        "explosion_potential_score",
        "tradeability_score",
        "pattern_strength_score",
        "volume_acceleration_score",
        "relative_strength_score",
        "persistence_scans",
        "exhaustion_penalty",
        "exhaustion_band",
        "relative_strength_percentile",
        "suppressed",
        "reasons",
        "components",
        "source_exchange",
        "scan_source",
        "measurement_only",
        "advisory_only",
        "affects_ranking",
        "affects_telegram",
        "affects_pending_setup",
        "trade_authority_changed",
        "production_execution_gate_changed",
    }
)

#: The only exchange the canonical episode cohort covers.
CANONICAL_EPISODE_SOURCE_EXCHANGE = "KRAKEN_SPOT"

#: The frozen source/authority flags and their production meanings. A snapshot is
#: a measurement: it must assert that it measured, advised and changed nothing.
#: Accepting any other combination would let a record claiming authority be
#: carried as lineage by an execution path.
CANONICAL_EPISODE_AUTHORITY_FLAGS: Mapping[str, bool] = MappingProxyType(
    {
        "measurement_only": True,
        "advisory_only": True,
        "affects_ranking": False,
        "affects_telegram": False,
        "affects_pending_setup": False,
        "trade_authority_changed": False,
        "production_execution_gate_changed": False,
    }
)

_FIELD_MISSING_PREFIX = "missing="
_FIELD_EXTRA_PREFIX = "extra="


def _digest(prefix: str, value: str, *, length: int) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}:{digest}"


def canonical_episode_id(*, schema_version: int, cohort_id: str, symbol: str) -> str:
    """The canonical ``EP:`` episode identity.

    Identity is a function of the schema version, the scan cohort and the pair,
    so the same pair in the same cohort always resolves to the same episode. The
    inputs are expected to be canonical already; this function never normalizes,
    because normalizing here would let a non-canonical payload derive a canonical
    id and hide the discrepancy.
    """
    return _digest(
        EPISODE_ID_PREFIX,
        f"{schema_version}|{cohort_id}|{symbol}",
        length=_EPISODE_DIGEST_LENGTH,
    )


def canonical_snapshot_id(*, schema_version: int, episode_id: str) -> str:
    """The canonical ``SNAP:`` snapshot identity.

    Derived from the schema version and the episode, so a snapshot identity is
    stable while its *contents* are not: the content hash is the separate
    ``PSNAP:`` value. A ``SNAP:`` identity must never stand in for that hash.
    """
    return _digest(
        SNAPSHOT_ID_PREFIX,
        f"{schema_version}|{episode_id}",
        length=_SNAPSHOT_DIGEST_LENGTH,
    )


def _canonical_identity(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a canonical string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not have leading or trailing whitespace")
    if not value:
        raise ValueError(f"{field_name} is required")
    return value


def parse_decision_at_utc(value: object) -> datetime:
    """Parse ``decision_at_utc`` as an aware timestamp, normalized to UTC.

    The producer serializes the decision instant with ``isoformat()``, so an
    offset form such as ``+00:00`` is the production shape. A ``Z`` suffix and any
    other aware offset are accepted because they denote the same instant, but a
    naive timestamp is refused: an unqualified local time cannot locate the
    evidence boundary.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("decision_at_utc must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("decision_at_utc must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("decision_at_utc must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def validate_canonical_episode_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one payload as a real canonical episode snapshot v1.

    Fails closed on: a non-mapping, a missing or extra field, an unsupported
    record type or schema version, a non-canonical identity string, a naive or
    malformed decision timestamp, a source/authority flag that does not carry its
    production meaning, content that is not canonically JSON-serializable or that
    contains NaN/Infinity, and any payload whose ``episode_id``/``snapshot_id`` do
    not match the deterministic identity derived from its own facts.

    The identity check is what makes the record self-proving: a payload cannot
    claim an episode or snapshot identity its own contents do not derive.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("canonical episode snapshot must be a mapping")
    fields = frozenset(str(key) for key in payload)
    missing = sorted(CANONICAL_EPISODE_SNAPSHOT_FIELDS - fields)
    extra = sorted(fields - CANONICAL_EPISODE_SNAPSHOT_FIELDS)
    if missing or extra:
        details = []
        if missing:
            details.append(_FIELD_MISSING_PREFIX + ",".join(missing))
        if extra:
            details.append(_FIELD_EXTRA_PREFIX + ",".join(extra))
        raise ValueError(
            "invalid canonical episode snapshot fields: " + "; ".join(details)
        )

    if payload.get("record_type") != CANONICAL_EPISODE_SNAPSHOT_RECORD_TYPE:
        raise ValueError(
            "canonical episode snapshot record_type must be "
            f"{CANONICAL_EPISODE_SNAPSHOT_RECORD_TYPE}"
        )
    schema_version = payload.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != CANONICAL_EPISODE_SNAPSHOT_SCHEMA_VERSION
    ):
        raise ValueError("unsupported canonical episode snapshot schema version")

    cohort_id = _canonical_identity(payload.get("cohort_id"), field_name="cohort_id")
    episode_id = _canonical_identity(payload.get("episode_id"), field_name="episode_id")
    snapshot_id = _canonical_identity(payload.get("snapshot_id"), field_name="snapshot_id")
    symbol = _canonical_identity(payload.get("symbol"), field_name="symbol")
    if symbol != symbol.upper():
        raise ValueError("symbol must be canonical uppercase")

    parse_decision_at_utc(payload.get("decision_at_utc"))

    if payload.get("source_exchange") != CANONICAL_EPISODE_SOURCE_EXCHANGE:
        raise ValueError(
            "canonical episode snapshot source_exchange must be "
            f"{CANONICAL_EPISODE_SOURCE_EXCHANGE}"
        )
    for flag_name, expected in CANONICAL_EPISODE_AUTHORITY_FLAGS.items():
        if payload.get(flag_name) is not expected:
            raise ValueError(
                f"canonical episode snapshot {flag_name} must be {expected}"
            )

    # Canonical serializability is a property of the record itself, not of how it
    # happens to be transmitted. NaN/Infinity are refused here rather than being
    # rendered as non-standard JSON tokens.
    try:
        json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "canonical episode snapshot is not canonically serializable"
        ) from exc

    expected_episode_id = canonical_episode_id(
        schema_version=schema_version, cohort_id=cohort_id, symbol=symbol
    )
    if episode_id != expected_episode_id:
        raise ValueError(
            "episode_id must be the canonical episode identity for its cohort and symbol"
        )
    expected_snapshot_id = canonical_snapshot_id(
        schema_version=schema_version, episode_id=episode_id
    )
    if snapshot_id != expected_snapshot_id:
        raise ValueError(
            "snapshot_id must be the canonical snapshot identity for its episode"
        )

    return dict(payload)


__all__ = [
    "CANONICAL_EPISODE_AUTHORITY_FLAGS",
    "CANONICAL_EPISODE_SNAPSHOT_FIELDS",
    "CANONICAL_EPISODE_SNAPSHOT_RECORD_TYPE",
    "CANONICAL_EPISODE_SNAPSHOT_SCHEMA_VERSION",
    "CANONICAL_EPISODE_SOURCE_EXCHANGE",
    "EPISODE_ID_PREFIX",
    "SNAPSHOT_ID_PREFIX",
    "canonical_episode_id",
    "canonical_snapshot_id",
    "parse_decision_at_utc",
    "validate_canonical_episode_snapshot",
]
