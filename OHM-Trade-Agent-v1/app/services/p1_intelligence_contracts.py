"""Canonical point-in-time contracts for the OHM P1 intelligence program.

These contracts deliberately contain no network clients, Telegram integration,
PendingSetup state, or execution authority. They are immutable research
snapshots emitted from an already-computed live decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping


SCHEMA_VERSION = 1


def require_utc(value: datetime, *, field_name: str) -> datetime:
    """Require an aware timestamp and normalize it to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_safe_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only JSON-safe scalar/list/dict values and reject non-finite floats."""
    if not value:
        return {}

    def clean(item: Any) -> Any:
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            return item if math.isfinite(item) else None
        if isinstance(item, Mapping):
            return {str(key): clean(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(val) for val in item]
        try:
            number = float(item)
        except (TypeError, ValueError):
            return str(item)
        return number if math.isfinite(number) else None

    return {str(key): clean(val) for key, val in value.items()}


@dataclass(frozen=True)
class LiveScanSnapshot:
    """One immutable, candidate-level Phase 1 decision-time snapshot."""

    schema_version: int
    decision_at_utc: str
    symbol: str
    reference_price: float | None
    candidate_rank: int
    universe_size: int
    stage: str
    pattern: str | None
    opportunity_score: int
    explosion_potential_score: int
    tradeability_score: int
    pattern_strength_score: int
    volume_acceleration_score: int
    relative_strength_score: int
    persistence_scans: int
    exhaustion_penalty: int
    exhaustion_band: str
    relative_strength_percentile: float | None
    liquidity_24h_usd_approx: float | None
    suppressed: bool
    reasons: tuple[str, ...]
    components: dict[str, Any]
    source_exchange: str = "KRAKEN_SPOT"
    scan_source: str = "LIVE"
    measurement_only: bool = True
    advisory_only: bool = True
    affects_ranking: bool = False
    affects_telegram: bool = False
    affects_pending_setup: bool = False
    trade_authority_changed: bool = False
    production_execution_gate_changed: bool = False

    @property
    def snapshot_id(self) -> str:
        identity = {
            "schema_version": self.schema_version,
            "decision_at_utc": self.decision_at_utc,
            "symbol": self.symbol,
            "candidate_rank": self.candidate_rank,
            "reference_price": self.reference_price,
            "stage": self.stage,
            "opportunity_score": self.opportunity_score,
        }
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["snapshot_id"] = self.snapshot_id
        payload["reasons"] = list(self.reasons)
        return payload


def build_live_scan_snapshot(
    candidate: Any,
    *,
    decision_at: datetime,
    candidate_rank: int,
    reference_prices: Mapping[str, float] | None = None,
    source_exchange: str = "KRAKEN_SPOT",
) -> LiveScanSnapshot:
    """Build a snapshot without consulting wall-clock time or external data.

    Non-finite numeric measurements are represented as ``None`` rather than
    emitting invalid JSON or aborting a ranked batch. Missingness remains
    explicit evidence; it is never promoted to a positive score.
    """
    decision = require_utc(decision_at, field_name="decision_at")
    if candidate_rank < 1:
        raise ValueError("candidate_rank must be >= 1")

    symbol = str(getattr(candidate, "symbol", "") or "").upper()
    if not symbol:
        raise ValueError("candidate symbol is required")

    price = (reference_prices or {}).get(symbol)
    if price is None:
        price = getattr(candidate, "reference_price", None)

    return LiveScanSnapshot(
        schema_version=SCHEMA_VERSION,
        decision_at_utc=decision.isoformat(),
        symbol=symbol,
        reference_price=_finite_float(price),
        candidate_rank=int(candidate_rank),
        universe_size=int(getattr(candidate, "universe_size", 0) or 0),
        stage=str(getattr(candidate, "stage", "") or ""),
        pattern=getattr(candidate, "pattern", None),
        opportunity_score=int(getattr(candidate, "opportunity_score", 0) or 0),
        explosion_potential_score=int(getattr(candidate, "explosion_potential_score", 0) or 0),
        tradeability_score=int(getattr(candidate, "tradeability_score", 0) or 0),
        pattern_strength_score=int(getattr(candidate, "pattern_strength_score", 0) or 0),
        volume_acceleration_score=int(getattr(candidate, "volume_acceleration_score", 0) or 0),
        relative_strength_score=int(getattr(candidate, "relative_strength_score", 0) or 0),
        persistence_scans=int(getattr(candidate, "persistence_scans", 0) or 0),
        exhaustion_penalty=int(getattr(candidate, "exhaustion_penalty", 0) or 0),
        exhaustion_band=str(getattr(candidate, "exhaustion_band", "") or ""),
        relative_strength_percentile=_finite_float(
            getattr(candidate, "relative_strength_percentile", None)
        ),
        liquidity_24h_usd_approx=_finite_float(
            getattr(candidate, "liquidity_24h_usd_approx", None)
        ),
        suppressed=bool(getattr(candidate, "suppressed", False)),
        reasons=tuple(str(item) for item in (getattr(candidate, "reasons", ()) or ())),
        components=_json_safe_mapping(getattr(candidate, "components", {}) or {}),
        source_exchange=str(source_exchange or "KRAKEN_SPOT").upper(),
    )


@dataclass(frozen=True)
class MarketDataQuery:
    """Exchange-agnostic, point-in-time market-data request."""

    symbol: str
    interval_minutes: int
    end_at_utc: datetime
    lookback_bars: int

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        if self.interval_minutes <= 0:
            raise ValueError("interval_minutes must be > 0")
        if self.lookback_bars <= 0:
            raise ValueError("lookback_bars must be > 0")
        object.__setattr__(
            self,
            "end_at_utc",
            require_utc(self.end_at_utc, field_name="end_at_utc"),
        )


@dataclass(frozen=True)
class MarketDataBar:
    opened_at_utc: datetime
    closed_at_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    def __post_init__(self) -> None:
        opened = require_utc(self.opened_at_utc, field_name="opened_at_utc")
        closed = require_utc(self.closed_at_utc, field_name="closed_at_utc")
        if closed <= opened:
            raise ValueError("bar close must be after bar open")
        object.__setattr__(self, "opened_at_utc", opened)
        object.__setattr__(self, "closed_at_utc", closed)


@dataclass(frozen=True)
class MarketDataSlice:
    """Completed bars returned by an adapter for one bounded query."""

    exchange: str
    canonical_symbol: str
    interval_minutes: int
    requested_end_at_utc: datetime
    fetched_at_utc: datetime
    bars: tuple[MarketDataBar, ...] = ()
    status: str = "AVAILABLE"
    error_type: str | None = None
    measurement_only: bool = True
    advisory_only: bool = True

    def __post_init__(self) -> None:
        end_at = require_utc(self.requested_end_at_utc, field_name="requested_end_at_utc")
        fetched = require_utc(self.fetched_at_utc, field_name="fetched_at_utc")
        object.__setattr__(self, "requested_end_at_utc", end_at)
        object.__setattr__(self, "fetched_at_utc", fetched)
        for bar in self.bars:
            if bar.closed_at_utc > end_at:
                raise ValueError("MarketDataSlice contains a bar closed after requested end time")


@dataclass(frozen=True)
class CatalystContext:
    """Context-only catalyst record. It carries no trade or ranking weight."""

    catalyst_id: str
    symbol: str
    source: str
    publication_at_utc: datetime
    observed_at_utc: datetime
    category: str
    headline: str
    event_at_utc: datetime | None = None
    source_reference: str | None = None
    linkage_confidence: str = "UNVERIFIED"
    measurement_only: bool = True
    context_only: bool = True
    numerical_trade_weight: float = field(default=0.0, init=False)
    affects_ranking: bool = field(default=False, init=False)
    affects_telegram: bool = field(default=False, init=False)
    trade_authority_changed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        publication = require_utc(self.publication_at_utc, field_name="publication_at_utc")
        observed = require_utc(self.observed_at_utc, field_name="observed_at_utc")
        object.__setattr__(self, "publication_at_utc", publication)
        object.__setattr__(self, "observed_at_utc", observed)
        if self.event_at_utc is not None:
            object.__setattr__(
                self,
                "event_at_utc",
                require_utc(self.event_at_utc, field_name="event_at_utc"),
            )
