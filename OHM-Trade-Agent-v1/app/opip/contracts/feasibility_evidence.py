"""R4-B0 canonical feasibility-evidence contract.

OWNER Decision 2 (R4-B0) replaces F5's architectural dependency on the legacy
scanner ``MarketSnapshot`` with ONE canonical typed record, ``FeasibilityEvidence``.
Decision 1 keeps ``FeatureSnapshot`` as detector/forecast feature evidence and
explicitly does NOT let it own margin, liquidity or execution feasibility; this
record is the separate typed projection that does.

What this record is
-------------------
The minimum explicit evidence F5's three required checks consume:

* ``MARKET_DATA`` — the market-data validity evidence;
* ``MARGIN_ELIGIBILITY`` — the margin-eligibility evidence (SHORT only);
* ``EXECUTION_LIQUIDITY`` — the execution-liquidity evidence;

anchored to explicit instrument identity, evaluation/cutoff instants, source
evidence references, an availability/missingness statement and a deterministic
``FEV:`` evidence fingerprint.

What this record is NOT
-----------------------
It is not a copy of ``MarketSnapshot``. It carries only the validated evidence
sub-records F5 already required, so every existing evaluator and threshold is
reused unchanged rather than rewritten (Decision 2). It performs no market read,
holds no clock and derives no missing value: absent evidence stays absent and is
never made favorable.

Legacy compatibility
--------------------
``feasibility_evidence_from_market_snapshot`` is the one transitional adapter. It
exists so legacy callers keep working; the target F5 contract no longer requires
``isinstance(evidence, MarketSnapshot)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import require_utc

FEASIBILITY_EVIDENCE_SCHEMA_VERSION = "feasibility-evidence-v1"
FEASIBILITY_EVIDENCE_ID_PREFIX = "FEV"

#: Availability of the evidence bundle as a whole. ``AVAILABLE`` is the only
#: state that may produce a non-abstaining F5 result, and it is a statement about
#: evidence presence, never a favorable judgement.
EVIDENCE_AVAILABLE = "AVAILABLE"
EVIDENCE_PARTIAL = "PARTIAL"
EVIDENCE_UNAVAILABLE = "UNAVAILABLE"

SUPPORTED_EVIDENCE_DIRECTIONS = frozenset({"LONG", "SHORT"})


class FeasibilityEvidenceError(ValueError):
    """A structural evidence violation. Always fails closed."""


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise FeasibilityEvidenceError(f"{field_name} must be non-empty text")
    return value


def _require_utc(value: Any, *, field_name: str) -> datetime:
    try:
        return require_utc(value, field_name=field_name)
    except Exception as exc:  # noqa: BLE001 - normalize to this contract's error
        raise FeasibilityEvidenceError(f"{field_name}: {exc}") from exc


def _lenient_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _lenient_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _lenient_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _lenient_text_tuple(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, str) for item in value
    ):
        return tuple(value)
    return None


def _lenient_float_token(value: Any) -> float | str | None:
    """A finite number, or its original text token when it is not finite.

    Structural, not judgemental: a rejected legacy record can carry a raw
    non-finite ``ticker_last``, and that fact must be part of the identity rather
    than being silently dropped or made finite.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else str(value)
    if isinstance(value, str):
        return value
    return None


@dataclass(frozen=True)
class FeasibilityEvidence:
    """The canonical typed feasibility-evidence record consumed by F5.

    The evidence sub-records are the existing validated types: reusing them keeps
    every evaluator and threshold unchanged. They are carried, never re-derived.
    """

    instrument_version_id: str
    venue_instrument_id: str
    direction: str
    evaluation_time: datetime
    source_cutoff: datetime
    source_snapshot_id: str
    source_evidence_refs: tuple[str, ...]

    market_data_validation: Any | None
    margin_validation_status: str | None
    margin_eligible: bool | None
    margin_venue_symbol: str | None
    margin_max_leverage: float | None
    execution_validation: Any | None

    availability: str
    missingness: tuple[str, ...] = ()

    #: Optional carried identity aliases, so F5's instrument-correspondence guard
    #: can compare every populated identifier the legacy path compared.
    kraken_public_symbol: str | None = None
    primary_pair: str | None = None

    schema_version: str = FEASIBILITY_EVIDENCE_SCHEMA_VERSION
    evidence_fingerprint: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != FEASIBILITY_EVIDENCE_SCHEMA_VERSION:
            raise FeasibilityEvidenceError(
                f"schema_version is not the ratified {FEASIBILITY_EVIDENCE_SCHEMA_VERSION}"
            )
        for name in ("instrument_version_id", "venue_instrument_id", "source_snapshot_id"):
            object.__setattr__(
                self, name, _require_text(getattr(self, name), field_name=name)
            )
        if not isinstance(self.direction, str) or self.direction not in (
            SUPPORTED_EVIDENCE_DIRECTIONS
        ):
            # The producer emits exact uppercase tokens; a case-folded or unknown
            # token is malformed and fails closed rather than being normalized.
            raise FeasibilityEvidenceError(
                "direction must be the exact token LONG or SHORT"
            )
        object.__setattr__(
            self,
            "evaluation_time",
            _require_utc(self.evaluation_time, field_name="evaluation_time"),
        )
        object.__setattr__(
            self, "source_cutoff", _require_utc(self.source_cutoff, field_name="source_cutoff")
        )
        if self.source_cutoff > self.evaluation_time:
            raise FeasibilityEvidenceError(
                "source_cutoff must not be later than the evaluation time"
            )
        if not isinstance(self.availability, str) or self.availability not in {
            EVIDENCE_AVAILABLE,
            EVIDENCE_PARTIAL,
            EVIDENCE_UNAVAILABLE,
        }:
            raise FeasibilityEvidenceError(
                "availability must be AVAILABLE, PARTIAL or UNAVAILABLE"
            )
        if not isinstance(self.source_evidence_refs, (list, tuple)) or not all(
            isinstance(item, str) for item in self.source_evidence_refs
        ):
            raise FeasibilityEvidenceError("source_evidence_refs must be text")
        object.__setattr__(self, "source_evidence_refs", tuple(self.source_evidence_refs))
        if not isinstance(self.missingness, (list, tuple)) or not all(
            isinstance(item, str) for item in self.missingness
        ):
            raise FeasibilityEvidenceError("missingness must be text")
        object.__setattr__(self, "missingness", tuple(self.missingness))
        expected = feasibility_evidence_fingerprint(self)
        if self.evidence_fingerprint == "":
            object.__setattr__(self, "evidence_fingerprint", expected)
        elif self.evidence_fingerprint != expected:
            raise FeasibilityEvidenceError(
                "evidence_fingerprint does not match its content; build evidence "
                "with feasibility_evidence_from_market_snapshot"
            )

    # --- compatibility surface ---------------------------------------------
    @property
    def trade_direction(self) -> str:
        """Legacy evaluator attribute name. Derived, never stored twice."""
        return self.direction

    @property
    def symbol(self) -> str:
        return self.venue_instrument_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_version_id": self.instrument_version_id,
            "venue_instrument_id": self.venue_instrument_id,
            "direction": self.direction,
            "evaluation_time": iso_z(self.evaluation_time, field_name="evaluation_time"),
            "source_cutoff": iso_z(self.source_cutoff, field_name="source_cutoff"),
            "source_snapshot_id": self.source_snapshot_id,
            "source_evidence_refs": list(self.source_evidence_refs),
            "availability": self.availability,
            "missingness": list(self.missingness),
            "evidence_fingerprint": self.evidence_fingerprint,
        }


def feasibility_evidence_summary(evidence: FeasibilityEvidence) -> dict[str, Any]:
    """The normalized F5-required evidence inputs used for the fingerprint.

    Deliberately lenient, exactly as the legacy summary was: it never raises, so
    building the fingerprint cannot pre-empt the ordered checks and mask a proven
    hard veto. Only well-typed primitives and primitive sequences are kept, so the
    fingerprint never depends on an object repr.
    """
    market = evidence.market_data_validation
    if market is None:
        market_summary: dict[str, Any] | None = None
    else:
        market_summary = {
            "status": _lenient_text(getattr(market, "status", None)),
            "qualified": _lenient_bool(getattr(market, "qualified", None)),
            "candle_count": _lenient_number(getattr(market, "candle_count", None)),
            "latest_candle_timestamp": _lenient_number(
                getattr(market, "latest_candle_timestamp", None)
            ),
            "latest_candle_age_seconds": _lenient_number(
                getattr(market, "latest_candle_age_seconds", None)
            ),
            "duplicate_timestamp_count": _lenient_number(
                getattr(market, "duplicate_timestamp_count", None)
            ),
            "gap_count": _lenient_number(getattr(market, "gap_count", None)),
            "invalid_ohlc_count": _lenient_number(
                getattr(market, "invalid_ohlc_count", None)
            ),
            "non_finite_value_count": _lenient_number(
                getattr(market, "non_finite_value_count", None)
            ),
            "largest_gap_seconds": _lenient_number(
                getattr(market, "largest_gap_seconds", None)
            ),
            "ticker_last": _lenient_float_token(getattr(market, "ticker_last", None)),
            "latest_ohlc_close": _lenient_number(
                getattr(market, "latest_ohlc_close", None)
            ),
            "ticker_vs_ohlc_difference_pct": _lenient_number(
                getattr(market, "ticker_vs_ohlc_difference_pct", None)
            ),
            "suspicious_spike_detected": _lenient_bool(
                getattr(market, "suspicious_spike_detected", None)
            ),
            "warnings": _lenient_text_tuple(getattr(market, "warnings", None)),
            "rejection_reasons": _lenient_text_tuple(
                getattr(market, "rejection_reasons", None)
            ),
        }

    execution = evidence.execution_validation
    if execution is None:
        execution_summary: dict[str, Any] | None = None
    else:
        execution_summary = {
            "status": _lenient_text(getattr(execution, "status", None)),
            "book_coverage_status": _lenient_text(
                getattr(execution, "book_coverage_status", None)
            ),
            "spread_bps": _lenient_number(getattr(execution, "spread_bps", None)),
            "buy_visible_coverage_pct": _lenient_number(
                getattr(execution, "buy_visible_coverage_pct", None)
            ),
            "sell_visible_coverage_pct": _lenient_number(
                getattr(execution, "sell_visible_coverage_pct", None)
            ),
            "buy_fully_covered": _lenient_bool(
                getattr(execution, "buy_fully_covered", None)
            ),
            "sell_fully_covered": _lenient_bool(
                getattr(execution, "sell_fully_covered", None)
            ),
            "short_round_trip_drag_pct": _lenient_number(
                getattr(
                    execution,
                    "estimated_visible_short_round_trip_market_drag_pct",
                    None,
                )
            ),
            "recent_trade_status": _lenient_text(
                getattr(execution, "recent_trade_status", None)
            ),
        }

    return {
        "direction": evidence.direction,
        "symbol": evidence.venue_instrument_id,
        "kraken_public_symbol": _lenient_text(evidence.kraken_public_symbol),
        "primary_pair": _lenient_text(evidence.primary_pair),
        "market": market_summary,
        "margin_status": _lenient_text(evidence.margin_validation_status),
        "margin_eligible": _lenient_bool(evidence.margin_eligible),
        "margin_venue_symbol": _lenient_text(evidence.margin_venue_symbol),
        "margin_max_leverage": _lenient_number(evidence.margin_max_leverage),
        "execution": execution_summary,
    }


def feasibility_evidence_fingerprint(evidence: FeasibilityEvidence) -> str:
    """The deterministic ``FEV:`` identity of one evidence bundle.

    A function of the explicit evidence epoch and the normalized evidence only, so
    the same observations always yield the same fingerprint and a tampered bundle
    fails closed.
    """
    payload = {
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
        "evidence": feasibility_evidence_summary(evidence),
    }
    return stable_hash(FEASIBILITY_EVIDENCE_ID_PREFIX, payload)


def feasibility_evidence_from_market_snapshot(
    snapshot: Any,
    *,
    source_cutoff: datetime,
    source_snapshot_id: str,
    evaluation_time: datetime | None = None,
    instrument_version_id: str | None = None,
    source_evidence_refs: tuple[str, ...] = (),
) -> FeasibilityEvidence:
    """Transitional adapter: the one legacy ``MarketSnapshot`` -> evidence bridge.

    It copies only the evidence F5 requires, performs no derivation and no market
    read, and never upgrades a missing field into a present one. A caller that can
    supply typed evidence directly should not use this.
    """
    direction = getattr(snapshot, "trade_direction", None)
    venue = str(getattr(snapshot, "symbol", "") or "")
    resolved_venue = venue or str(getattr(snapshot, "primary_pair", "") or "")
    if not resolved_venue:
        raise FeasibilityEvidenceError(
            "legacy snapshot carries no usable venue instrument identity"
        )
    market = getattr(snapshot, "market_data_validation", None)
    execution = getattr(snapshot, "execution_validation", None)
    missingness: list[str] = []
    if market is None:
        missingness.append("market_data_validation")
    if execution is None:
        missingness.append("execution_validation")
    if direction == "SHORT" and getattr(snapshot, "margin_validation_status", None) is None:
        missingness.append("margin_validation_status")

    resolved_cutoff = _require_utc(source_cutoff, field_name="source_cutoff")
    resolved_evaluation = _require_utc(
        evaluation_time if evaluation_time is not None else source_cutoff,
        field_name="evaluation_time",
    )
    if resolved_cutoff > resolved_evaluation:
        raise FeasibilityEvidenceError(
            "source_cutoff must not be later than the evaluation time"
        )
    return FeasibilityEvidence(
        instrument_version_id=str(
            instrument_version_id or getattr(snapshot, "instrument_version_id", "") or resolved_venue
        ),
        venue_instrument_id=resolved_venue,
        direction=direction,
        evaluation_time=resolved_evaluation,
        source_cutoff=resolved_cutoff,
        source_snapshot_id=source_snapshot_id,
        source_evidence_refs=tuple(source_evidence_refs),
        market_data_validation=market,
        margin_validation_status=getattr(snapshot, "margin_validation_status", None),
        margin_eligible=getattr(snapshot, "margin_eligible", None),
        margin_venue_symbol=getattr(snapshot, "margin_venue_symbol", None),
        margin_max_leverage=getattr(snapshot, "margin_max_leverage", None),
        execution_validation=execution,
        availability=EVIDENCE_AVAILABLE if not missingness else EVIDENCE_PARTIAL,
        missingness=tuple(missingness),
        kraken_public_symbol=getattr(snapshot, "kraken_public_symbol", None),
        primary_pair=getattr(snapshot, "primary_pair", None),
    )


__all__ = [
    "EVIDENCE_AVAILABLE",
    "EVIDENCE_PARTIAL",
    "EVIDENCE_UNAVAILABLE",
    "FEASIBILITY_EVIDENCE_ID_PREFIX",
    "FEASIBILITY_EVIDENCE_SCHEMA_VERSION",
    "FeasibilityEvidence",
    "FeasibilityEvidenceError",
    "feasibility_evidence_fingerprint",
    "feasibility_evidence_from_market_snapshot",
    "feasibility_evidence_summary",
]
