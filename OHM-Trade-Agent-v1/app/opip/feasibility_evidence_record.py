"""Durable ``FeasibilityEvidence`` record codec (R4-B2 Slice 3A).

The frozen F5 contract ``FeasibilityEvidence`` carries two identities that must
never be conflated:

* ``evidence_fingerprint`` (``FEV:``) — the frozen F5 *semantic* fingerprint. It
  deliberately covers only the F5-normalized evidence subset
  (``feasibility_evidence_summary``), because widening it would change F5
  semantics. It is the F5 lineage identity.
* ``payload_hash`` (``FEVH:``) — a new *exact durable-content* hash covering every
  persisted substantive field, including the nested validation records in full.
  Any persisted-content mutation changes it.

This mirrors the repository's Paper-v2 decision-snapshot precedent: a semantic
identity plus an independent exact-content hash. ``FeasibilityEvidence.to_dict()``
is NOT a persistence codec (it omits the substantive validation records), so the
durable wire schema is frozen here explicitly. It is deliberately enumerated field
by field — never derived from ``dataclasses.fields()`` — so a future Python field
addition cannot silently change an already-ratified event schema.

This module is pure: no market read, no clock, no writer, no authority.

It lives at ``app/opip/`` rather than ``app/opip/contracts/`` because strict
reconstruction rebuilds the concrete ``MarketDataValidation`` and
``ExecutionValidation`` types, and the frozen contracts layer is dependency-locked
against ``app.scanner`` (see ``test_contracts_layer_has_no_downstream_dependencies``).
The frozen F5 contract ``FeasibilityEvidence`` itself stays in the contracts layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.opip.contracts.feasibility_evidence import (
    EVIDENCE_AVAILABLE,
    EVIDENCE_PARTIAL,
    EVIDENCE_UNAVAILABLE,
    FeasibilityEvidence,
    FeasibilityEvidenceError,
    feasibility_evidence_fingerprint,
)
from app.opip.contracts.serialization import canonical_json_bytes, iso_z, stable_hash
from app.opip.contracts.temporal import require_utc
from app.scanner.execution_validation import ExecutionValidation
from app.scanner.market_data_validation import MarketDataValidation

RECORD_TYPE = "FeasibilityEvidence"
RECORD_SCHEMA_VERSION = "feasibility-evidence-record-v1"
PAYLOAD_HASH_PREFIX = "FEVH"

#: Explicit, frozen top-level durable body fields (in canonical order). This is
#: NOT derived from the dataclass so a Python field addition cannot silently
#: mutate the wire schema.
FEASIBILITY_EVIDENCE_BODY_FIELDS: tuple[str, ...] = (
    "schema_version",
    "instrument_version_id",
    "venue_instrument_id",
    "direction",
    "evaluation_time",
    "source_cutoff",
    "source_snapshot_id",
    "source_evidence_refs",
    "availability",
    "missingness",
    "kraken_public_symbol",
    "primary_pair",
    "margin_validation_status",
    "margin_eligible",
    "margin_venue_symbol",
    "margin_max_leverage",
    "evidence_fingerprint",
    "market_data_validation",
    "execution_validation",
)

#: Explicit, frozen nested ``MarketDataValidation`` fields and value kinds.
MARKET_DATA_VALIDATION_FIELDS: tuple[str, ...] = (
    "status",
    "qualified",
    "warnings",
    "rejection_reasons",
    "candle_count",
    "latest_candle_timestamp",
    "latest_candle_age_seconds",
    "duplicate_timestamp_count",
    "gap_count",
    "largest_gap_seconds",
    "invalid_ohlc_count",
    "non_finite_value_count",
    "ticker_last",
    "latest_ohlc_close",
    "ticker_vs_ohlc_difference_pct",
    "suspicious_spike_detected",
)

#: Explicit, frozen nested ``ExecutionValidation`` fields and value kinds.
EXECUTION_VALIDATION_FIELDS: tuple[str, ...] = (
    "status",
    "book_coverage_status",
    "warnings",
    "best_bid",
    "best_ask",
    "mid_price",
    "absolute_spread",
    "spread_pct",
    "spread_bps",
    "visible_bid_notional",
    "visible_ask_notional",
    "bid_depth_025_usd",
    "ask_depth_025_usd",
    "bid_depth_025_complete",
    "ask_depth_025_complete",
    "bid_depth_050_usd",
    "ask_depth_050_usd",
    "bid_depth_050_complete",
    "ask_depth_050_complete",
    "validation_notional_usd",
    "buy_vwap",
    "sell_vwap",
    "buy_market_impact_pct",
    "sell_market_impact_pct",
    "buy_visible_coverage_pct",
    "sell_visible_coverage_pct",
    "buy_fully_covered",
    "sell_fully_covered",
    "estimated_visible_round_trip_market_drag_pct",
    "estimated_visible_short_round_trip_market_drag_pct",
    "recent_trade_status",
    "latest_trade_price",
    "latest_trade_age_seconds",
    "recent_trade_count",
)

#: Value kinds per persisted field. ``number`` rejects non-finite values; ``bool``
#: is an exact bool (never an int); ``exact_int`` rejects bools and floats.
#: ``float_token`` mirrors the frozen F5 ``_lenient_float_token`` domain: a finite
#: number, or the original text token when the underlying value is non-finite,
#: because F5 explicitly admits a rejected record's raw non-finite ``ticker_last``
#: into the identity rather than making it finite or dropping it.
_MARKET_DATA_VALIDATION_KINDS: dict[str, str] = {
    "status": "market_status",
    "qualified": "bool",
    "warnings": "text_list",
    "rejection_reasons": "text_list",
    "candle_count": "exact_int",
    "latest_candle_timestamp": "opt_int",
    "latest_candle_age_seconds": "opt_number",
    "duplicate_timestamp_count": "exact_int",
    "gap_count": "exact_int",
    "largest_gap_seconds": "number",
    "invalid_ohlc_count": "exact_int",
    "non_finite_value_count": "exact_int",
    "ticker_last": "float_token",
    "latest_ohlc_close": "opt_number",
    "ticker_vs_ohlc_difference_pct": "opt_number",
    "suspicious_spike_detected": "bool",
}

_EXECUTION_VALIDATION_KINDS: dict[str, str] = {
    "status": "execution_status",
    "book_coverage_status": "book_coverage",
    "warnings": "text_list",
    "best_bid": "opt_number",
    "best_ask": "opt_number",
    "mid_price": "opt_number",
    "absolute_spread": "opt_number",
    "spread_pct": "opt_number",
    "spread_bps": "opt_number",
    "visible_bid_notional": "opt_number",
    "visible_ask_notional": "opt_number",
    "bid_depth_025_usd": "opt_number",
    "ask_depth_025_usd": "opt_number",
    "bid_depth_025_complete": "bool",
    "ask_depth_025_complete": "bool",
    "bid_depth_050_usd": "opt_number",
    "ask_depth_050_usd": "opt_number",
    "bid_depth_050_complete": "bool",
    "ask_depth_050_complete": "bool",
    "validation_notional_usd": "number",
    "buy_vwap": "opt_number",
    "sell_vwap": "opt_number",
    "buy_market_impact_pct": "opt_number",
    "sell_market_impact_pct": "opt_number",
    "buy_visible_coverage_pct": "opt_number",
    "sell_visible_coverage_pct": "opt_number",
    "buy_fully_covered": "bool",
    "sell_fully_covered": "bool",
    "estimated_visible_round_trip_market_drag_pct": "opt_number",
    "estimated_visible_short_round_trip_market_drag_pct": "opt_number",
    "recent_trade_status": "recent_trade",
    "latest_trade_price": "opt_number",
    "latest_trade_age_seconds": "opt_number",
    "recent_trade_count": "exact_int",
}

#: Frozen enum/status token vocabularies, matching the scanner validation
#: contracts exactly (``app/scanner/market_data_validation.py`` and
#: ``app/scanner/execution_validation.py``). Enumerated literally rather than
#: imported, because the two scanner modules reuse the same constant names and
#: this is a frozen wire vocabulary.
MARKET_STATUS_TOKENS: frozenset[str] = frozenset({"PASS", "WARN", "REJECT"})
EXECUTION_STATUS_TOKENS: frozenset[str] = frozenset({"VALID", "UNAVAILABLE", "INVALID"})
BOOK_COVERAGE_TOKENS: frozenset[str] = frozenset(
    {"UNAVAILABLE", "INSUFFICIENT", "COMPLETE", "PARTIAL"}
)
RECENT_TRADE_TOKENS: frozenset[str] = frozenset({"UNAVAILABLE", "WARN", "FRESH"})

_TOKEN_KINDS: dict[str, frozenset[str]] = {
    "market_status": MARKET_STATUS_TOKENS,
    "execution_status": EXECUTION_STATUS_TOKENS,
    "book_coverage": BOOK_COVERAGE_TOKENS,
    "recent_trade": RECENT_TRADE_TOKENS,
}

#: Explicit top-level value kinds.
_BODY_KINDS: dict[str, str] = {
    "schema_version": "text",
    "instrument_version_id": "text",
    "venue_instrument_id": "text",
    "direction": "text",
    "evaluation_time": "utc",
    "source_cutoff": "utc",
    "source_snapshot_id": "text",
    "evidence_fingerprint": "text",
    "availability": "availability",
    "kraken_public_symbol": "opt_text",
    "primary_pair": "opt_text",
    "margin_validation_status": "opt_text",
    "margin_eligible": "opt_bool",
    "margin_venue_symbol": "opt_text",
    "margin_max_leverage": "opt_number",
    "source_evidence_refs": "text_list",
    "missingness": "text_list",
}

_WRAPPER_FIELDS: frozenset[str] = frozenset(
    {"record_type", "record_schema_version", "evidence_fingerprint", "payload_hash", "evidence"}
)


class FeasibilityEvidenceRecordError(ValueError):
    """A durable feasibility-evidence record violation. Always fails closed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FeasibilityEvidenceRecordError(message)


def feasibility_evidence_payload_hash(body: Mapping[str, Any]) -> str:
    """The exact durable-content hash of one evidence body (the ``FEVH:`` identity).

    Covers every persisted substantive field, including the complete nested
    validation records. It is deliberately independent of the F5 semantic
    fingerprint: a mutation the F5 summary ignores still changes this hash.
    """
    return stable_hash(PAYLOAD_HASH_PREFIX, dict(body))


def _number(value: Any, *, field_name: str) -> float:
    _require(
        not isinstance(value, bool) and isinstance(value, (int, float)),
        f"{field_name} must be a finite number",
    )
    number = float(value)
    _require(number == number and number not in (float("inf"), float("-inf")), f"{field_name} must be finite")
    return number


def _opt_number(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    return _number(value, field_name=field_name)


def _exact_int(value: Any, *, field_name: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{field_name} must be an integer")
    return int(value)


def _opt_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    return _exact_int(value, field_name=field_name)


def _text(value: Any, *, field_name: str) -> str:
    _require(isinstance(value, str) and value.strip() != "", f"{field_name} must be non-empty text")
    return value


def _opt_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name=field_name)


def _bool(value: Any, *, field_name: str) -> bool:
    _require(isinstance(value, bool), f"{field_name} must be a boolean")
    return value


def _opt_bool(value: Any, *, field_name: str) -> bool | None:
    if value is None:
        return None
    return _bool(value, field_name=field_name)


def _text_list(value: Any, *, field_name: str) -> list[str]:
    _require(isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value), f"{field_name} must be text")
    return list(value)


def _is_finite(number: float) -> bool:
    return number == number and number not in (float("inf"), float("-inf"))


def _float_token(value: Any, *, field_name: str) -> float | str | None:
    """Normalize to the frozen F5 ``_lenient_float_token`` domain.

    A finite number stays a number; a non-finite float becomes its own text token
    (matching ``str(value)``) because canonical serialization forbids non-finite
    numbers and F5 fingerprints the token rather than dropping it; a text token is
    passed through. ``None`` stays ``None``.
    """
    if value is None:
        return None
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        number = float(value)
        return number if _is_finite(number) else str(value)
    if isinstance(value, str) and value.strip() != "":
        return value
    raise FeasibilityEvidenceRecordError(f"{field_name} must be a finite number or its text token")


def _validate_kind(kind: str, value: Any, *, field_name: str) -> Any:
    if kind == "text":
        return _text(value, field_name=field_name)
    if kind == "opt_text":
        return _opt_text(value, field_name=field_name)
    if kind == "bool":
        return _bool(value, field_name=field_name)
    if kind == "opt_bool":
        return _opt_bool(value, field_name=field_name)
    if kind == "exact_int":
        return _exact_int(value, field_name=field_name)
    if kind == "opt_int":
        return _opt_int(value, field_name=field_name)
    if kind == "number":
        return _number(value, field_name=field_name)
    if kind == "opt_number":
        return _opt_number(value, field_name=field_name)
    if kind == "text_list":
        return _text_list(value, field_name=field_name)
    if kind == "float_token":
        return _float_token(value, field_name=field_name)
    if kind == "availability":
        _require(
            value in {EVIDENCE_AVAILABLE, EVIDENCE_PARTIAL, EVIDENCE_UNAVAILABLE},
            f"{field_name} must be AVAILABLE, PARTIAL or UNAVAILABLE",
        )
        return value
    if kind in _TOKEN_KINDS:
        allowed = _TOKEN_KINDS[kind]
        _require(isinstance(value, str) and value in allowed, f"{field_name} is not a valid {kind} token")
        return value
    if kind == "utc":
        return _parse_utc(value, field_name=field_name)
    raise FeasibilityEvidenceRecordError(f"unknown field kind {kind!r} for {field_name}")


def _serialize_nested(obj: Any | None, fields: tuple[str, ...], kinds: Mapping[str, str], *, label: str) -> dict | None:
    if obj is None:
        return None
    body: dict[str, Any] = {}
    for name in fields:
        _require(hasattr(obj, name), f"{label} is missing field {name!r}")
        body[name] = getattr(obj, name)
    return body


def build_feasibility_evidence_payload(evidence: FeasibilityEvidence) -> dict[str, Any]:
    """Serialize one ``FeasibilityEvidence`` into the frozen durable wrapper."""
    _require(isinstance(evidence, FeasibilityEvidence), "evidence must be a FeasibilityEvidence")
    body: dict[str, Any] = {
        "schema_version": evidence.schema_version,
        "instrument_version_id": evidence.instrument_version_id,
        "venue_instrument_id": evidence.venue_instrument_id,
        "direction": evidence.direction,
        "evaluation_time": iso_z(evidence.evaluation_time, field_name="evaluation_time"),
        "source_cutoff": iso_z(evidence.source_cutoff, field_name="source_cutoff"),
        "source_snapshot_id": evidence.source_snapshot_id,
        "source_evidence_refs": list(evidence.source_evidence_refs),
        "availability": evidence.availability,
        "missingness": list(evidence.missingness),
        "kraken_public_symbol": evidence.kraken_public_symbol,
        "primary_pair": evidence.primary_pair,
        "margin_validation_status": evidence.margin_validation_status,
        "margin_eligible": evidence.margin_eligible,
        "margin_venue_symbol": evidence.margin_venue_symbol,
        "margin_max_leverage": evidence.margin_max_leverage,
        "evidence_fingerprint": evidence.evidence_fingerprint,
        "market_data_validation": _serialize_nested(
            evidence.market_data_validation,
            MARKET_DATA_VALIDATION_FIELDS,
            _MARKET_DATA_VALIDATION_KINDS,
            label="market_data_validation",
        ),
        "execution_validation": _serialize_nested(
            evidence.execution_validation,
            EXECUTION_VALIDATION_FIELDS,
            _EXECUTION_VALIDATION_KINDS,
            label="execution_validation",
        ),
    }
    # Normalize every value through its kind so the persisted representation is
    # canonical and a non-finite value fails closed here.
    normalized: dict[str, Any] = {}
    for name in FEASIBILITY_EVIDENCE_BODY_FIELDS:
        if name in ("market_data_validation", "execution_validation"):
            normalized[name] = _validate_nested(body[name], name)
        else:
            normalized[name] = _validate_kind(_BODY_KINDS[name], body[name], field_name=name)
    return {
        "record_type": RECORD_TYPE,
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "evidence_fingerprint": evidence.evidence_fingerprint,
        "payload_hash": feasibility_evidence_payload_hash(normalized),
        "evidence": normalized,
    }


def _validate_nested(value: Any, label: str) -> dict | None:
    if value is None:
        return None
    fields = MARKET_DATA_VALIDATION_FIELDS if label == "market_data_validation" else EXECUTION_VALIDATION_FIELDS
    kinds = _MARKET_DATA_VALIDATION_KINDS if label == "market_data_validation" else _EXECUTION_VALIDATION_KINDS
    _require(isinstance(value, Mapping), f"{label} must be a mapping or null")
    _require(set(value) == set(fields), f"{label} has an unexpected field set")
    return {name: _validate_kind(kinds[name], value[name], field_name=f"{label}.{name}") for name in fields}


def _parse_utc(value: Any, *, field_name: str) -> str:
    """Validate and canonicalize one ISO timestamp.

    Canonicalizing here means ``validate`` and reconstruction agree: a record
    carrying a non-canonical instant (for example ``+00:00`` instead of ``Z``) or a
    naive/malformed timestamp fails closed in both paths rather than being accepted
    on write and rejected on read.
    """
    _require(isinstance(value, str) and value.strip() != "", f"{field_name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return iso_z(parsed, field_name=field_name)
    except Exception as exc:  # noqa: BLE001 - normalize to this contract's error
        raise FeasibilityEvidenceRecordError(f"{field_name}: {exc}") from exc


def _deserialize_float_token(value: Any) -> Any:
    """Restore a persisted ``float_token`` to the value F5 would have derived.

    A finite numeric string stays a string (it was a genuine text token); a
    non-finite numeric token is restored to the float F5's ``_lenient_float_token``
    derives from it, so a non-finite ``ticker_last`` round-trips to an equal object.
    """
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return value
        if not _is_finite(number):
            return number
    return value


def validate_feasibility_evidence_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Structurally validate one durable record. Fails closed on any violation."""
    _require(isinstance(payload, Mapping), "record must be a mapping")
    _require(set(payload) == _WRAPPER_FIELDS, "record has an unexpected field set")
    _require(payload["record_type"] == RECORD_TYPE, "record_type is not FeasibilityEvidence")
    _require(
        payload["record_schema_version"] == RECORD_SCHEMA_VERSION,
        "record_schema_version is not the ratified version",
    )
    wrapper_fingerprint = _text(payload["evidence_fingerprint"], field_name="evidence_fingerprint")
    payload_hash = _text(payload["payload_hash"], field_name="payload_hash")
    _require(payload_hash.startswith(f"{PAYLOAD_HASH_PREFIX}:"), "payload_hash has the wrong domain")
    _require(isinstance(payload["evidence"], Mapping), "evidence must be a mapping")

    body = payload["evidence"]
    _require(set(body) == set(FEASIBILITY_EVIDENCE_BODY_FIELDS), "evidence has an unexpected field set")
    normalized: dict[str, Any] = {}
    for name in FEASIBILITY_EVIDENCE_BODY_FIELDS:
        if name in ("market_data_validation", "execution_validation"):
            normalized[name] = _validate_nested(body[name], name)
        else:
            normalized[name] = _validate_kind(_BODY_KINDS[name], body[name], field_name=name)

    # The wrapper duplicate must agree with the body, and the exact-content hash
    # must match the recomputed one.
    _require(wrapper_fingerprint == normalized["evidence_fingerprint"], "wrapper evidence_fingerprint disagrees with the body")
    expected_hash = feasibility_evidence_payload_hash(normalized)
    _require(payload_hash == expected_hash, "payload_hash does not match the persisted content")

    rebuilt = {
        "record_type": RECORD_TYPE,
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "evidence_fingerprint": wrapper_fingerprint,
        "payload_hash": payload_hash,
        "evidence": normalized,
    }
    return rebuilt


def _reconstruct_nested(value: Mapping[str, Any] | None, label: str) -> Any | None:
    if value is None:
        return None
    if label == "market_data_validation":
        fields = {name: value[name] for name in MARKET_DATA_VALIDATION_FIELDS}
        fields["ticker_last"] = _deserialize_float_token(fields["ticker_last"])
        return MarketDataValidation(**fields)
    return ExecutionValidation(**{name: value[name] for name in EXECUTION_VALIDATION_FIELDS})


def feasibility_evidence_from_payload(payload: Mapping[str, Any]) -> FeasibilityEvidence:
    """Strictly reconstruct the exact typed F5 evidence from one durable record.

    Three independent integrity checks are performed, and the third never replaces
    the second:

    1. the reconstructed F5 ``evidence_fingerprint`` equals the declared one;
    2. the rebuilt exact ``payload_hash`` equals the declared one;
    3. rebuilding the canonical wrapper from the reconstructed evidence yields the
       same normalized wrapper.
    """
    rebuilt = validate_feasibility_evidence_payload(payload)
    body = rebuilt["evidence"]
    try:
        evidence = FeasibilityEvidence(
            instrument_version_id=body["instrument_version_id"],
            venue_instrument_id=body["venue_instrument_id"],
            direction=body["direction"],
            evaluation_time=require_utc(datetime.fromisoformat(body["evaluation_time"].replace("Z", "+00:00")), field_name="evaluation_time"),
            source_cutoff=require_utc(datetime.fromisoformat(body["source_cutoff"].replace("Z", "+00:00")), field_name="source_cutoff"),
            source_snapshot_id=body["source_snapshot_id"],
            source_evidence_refs=tuple(body["source_evidence_refs"]),
            market_data_validation=_reconstruct_nested(body["market_data_validation"], "market_data_validation"),
            margin_validation_status=body["margin_validation_status"],
            margin_eligible=body["margin_eligible"],
            margin_venue_symbol=body["margin_venue_symbol"],
            margin_max_leverage=body["margin_max_leverage"],
            execution_validation=_reconstruct_nested(body["execution_validation"], "execution_validation"),
            availability=body["availability"],
            missingness=tuple(body["missingness"]),
            kraken_public_symbol=body["kraken_public_symbol"],
            primary_pair=body["primary_pair"],
            schema_version=body["schema_version"],
            evidence_fingerprint=body["evidence_fingerprint"],
        )
    except FeasibilityEvidenceError as exc:
        raise FeasibilityEvidenceRecordError(f"reconstructed evidence is not contract-valid: {exc}") from exc

    # 1. F5 semantic fingerprint.
    expected_fev = feasibility_evidence_fingerprint(evidence)
    _require(
        body["evidence_fingerprint"] == expected_fev,
        "evidence_fingerprint does not match the reconstructed F5 evidence",
    )
    # 2. Exact durable payload hash.
    rebuilt_wrapper = build_feasibility_evidence_payload(evidence)
    _require(
        rebuilt_wrapper["payload_hash"] == rebuilt["payload_hash"],
        "payload_hash does not match the reconstructed durable content",
    )
    # 3. Canonical wrapper equality (does not replace check 2).
    _require(
        canonical_json_bytes(rebuilt_wrapper) == canonical_json_bytes(rebuilt),
        "rebuilt wrapper is not canonically identical to the persisted record",
    )
    return evidence


__all__ = [
    "EXECUTION_VALIDATION_FIELDS",
    "FEASIBILITY_EVIDENCE_BODY_FIELDS",
    "MARKET_DATA_VALIDATION_FIELDS",
    "PAYLOAD_HASH_PREFIX",
    "RECORD_SCHEMA_VERSION",
    "RECORD_TYPE",
    "FeasibilityEvidenceRecordError",
    "build_feasibility_evidence_payload",
    "feasibility_evidence_from_payload",
    "feasibility_evidence_payload_hash",
    "validate_feasibility_evidence_payload",
]
