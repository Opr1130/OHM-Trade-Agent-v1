"""Forward decision telemetry for Signal Quality v1 (Phase 3A).

Phase 2 reconstructs what OHM *would* have decided from the event-sampled
JSONL stream. That reconstruction is an approximation - it carries values
forward across gaps, and it cannot see anything the live process didn't
persist. This module exists to remove that approximation going forward: it
records the *actual* live decision state, at the moment the live scan
produced it, so future analysis has ground truth instead of only replay.

Three properties make this safe to turn on:

* **Dark by default.** ``DECISION_TELEMETRY_V1_ENABLED`` defaults to False.
  Composed with ``SIGNAL_QUALITY_V1_ENABLED`` already defaulting to False and
  already making ``full_market.signal_quality_candidates`` empty when off,
  both flags default off and either one being off writes nothing.
* **Fail-soft.** Every failure mode - a permissions error, a full disk, a
  malformed candidate - is caught here. A telemetry failure can change what
  gets logged; it can never change what gets scored or alerted, because this
  module has no return value the caller could act on and no side effect
  besides the append itself.
* **One-directional.** Nothing in this module reads the telemetry file back.
  There is no function here that could feed a written record into a future
  decision, by construction - the only public function takes already-computed
  candidates and returns a count.

The write path mirrors ``app/services/candidate_trace.py``: an append-only
JSONL file behind a ``registry_lock``, one line per record, secret-free.

``price`` is the exact same-scan observation price each candidate was scored
from - not a value carried by ``SignalQualityCandidate`` itself (Phase 1's
scorer has no price field, and never has), and not a second market-data
lookup. ``app.services.full_market_observation.process_full_market_observations``
already holds this price in memory for the same scan it derives features
from, and exposes it read-only on ``FullMarketResult.signal_quality_reference_prices``
(a ``{symbol: price}`` mapping, empty unless Signal Quality is enabled). This
module only ever reads that mapping; it never fetches, derives, or requests a
price of its own. See SIGNAL_QUALITY_PHASE3A.md §2.5 for the full data path.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.services.registry_io import registry_lock


SCHEMA_VERSION = 1
DEFAULT_TELEMETRY_FILE = Path("/app/data/decision_telemetry.jsonl")


@dataclass(frozen=True)
class DecisionTelemetryRecord:
    """One row: everything needed to reconstruct what OHM knew about one
    symbol at one live scan, without depending on JSONL reconstruction.
    """

    schema_version: int
    recorded_at: str
    scan_source: str  # "LIVE" - reserved for future non-live sources
    symbol: str
    # The same-scan observation price this candidate was scored from, read
    # from FullMarketResult.signal_quality_reference_prices (see module
    # docstring). None only when that mapping has no entry for this symbol -
    # e.g. a candidate built outside the live scan_movers.py path, such as in
    # a unit test that does not supply one.
    price: float | None
    liquidity_24h_usd_approx: float
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
    relative_strength_percentile: float
    universe_size: int
    reasons: tuple[str, ...]
    suppressed: bool
    signal_quality_enabled: bool
    early_alerts_enabled: bool
    # Invariants asserted, not just documented: this record can never carry
    # execution authority, no matter what future fields are added to it.
    advisory_only: bool = True
    weights_are_calibrated: bool = False
    trade_authority_changed: bool = False
    production_execution_gate_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def build_telemetry_record(
    candidate: Any,
    *,
    settings: Any,
    reference_prices: Mapping[str, float] | None = None,
    now: datetime | None = None,
) -> DecisionTelemetryRecord:
    """Convert one live ``SignalQualityCandidate`` into a telemetry record.

    Reads only public attributes already present on the candidate (the same
    object ``scan_movers.py`` already has after scoring); derives nothing new
    and calls no external service. ``reference_prices`` is the same-scan
    ``{symbol: price}`` mapping from
    ``FullMarketResult.signal_quality_reference_prices`` - this function only
    reads it by key, it never fetches or computes a price itself.
    """
    now = now or datetime.now(timezone.utc)
    symbol = str(getattr(candidate, "symbol", "") or "").upper()
    price = (reference_prices or {}).get(symbol)
    if price is None:
        # Opportunistic fallback only: no candidate carries this attribute
        # today, but this keeps the record correct with no call-site change
        # if one ever does.
        price = getattr(candidate, "reference_price", None)
    return DecisionTelemetryRecord(
        schema_version=SCHEMA_VERSION,
        recorded_at=now.astimezone(timezone.utc).isoformat(),
        scan_source="LIVE",
        symbol=symbol,
        price=(float(price) if price is not None else None),
        liquidity_24h_usd_approx=float(getattr(candidate, "liquidity_24h_usd_approx", 0.0) or 0.0),
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
        relative_strength_percentile=float(
            getattr(candidate, "relative_strength_percentile", 0.0) or 0.0
        ),
        universe_size=int(getattr(candidate, "universe_size", 0) or 0),
        reasons=tuple(getattr(candidate, "reasons", ()) or ()),
        suppressed=bool(getattr(candidate, "suppressed", False)),
        signal_quality_enabled=bool(getattr(settings, "signal_quality_v1_enabled", False)),
        early_alerts_enabled=bool(getattr(settings, "signal_quality_early_alerts_enabled", False)),
    )


def record_decision_telemetry(
    candidates: Iterable[Any],
    *,
    settings: Any,
    reference_prices: Mapping[str, float] | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Append one telemetry line per candidate. Returns the count written.

    ``reference_prices`` should be the same-scan
    ``FullMarketResult.signal_quality_reference_prices`` mapping the caller
    already has; passed straight through to ``build_telemetry_record`` with
    no lookup, fetch, or derivation performed here.

    Fail-soft and dark-by-default in the same call: if the flag is off, or
    anything at all goes wrong, this returns 0 and raises nothing. Callers
    never need their own try/except around this - it cannot escape here -
    but ``scan_movers.py`` wraps it anyway as defence in depth, per the
    approved design.
    """
    if not bool(getattr(settings, "decision_telemetry_v1_enabled", False)):
        return 0
    try:
        rows = list(candidates)
        if not rows:
            return 0
        target = path or DEFAULT_TELEMETRY_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        lock = target.parent / f".{target.name}.lock"
        written = 0
        with registry_lock(lock):
            with target.open("a", encoding="utf-8") as handle:
                for candidate in rows:
                    record = build_telemetry_record(
                        candidate,
                        settings=settings,
                        reference_prices=reference_prices,
                        now=now,
                    )
                    handle.write(
                        json.dumps(record.as_dict(), sort_keys=True, default=str, allow_nan=False)
                        + "\n"
                    )
                    written += 1
                handle.flush()
        return written
    except Exception:
        # Fail-soft by design: telemetry must never affect scoring or
        # alerting, so nothing here is allowed to propagate.
        return 0
